from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from typing import Any

from qdrant_mcp.allure_client import AllureTestOpsClient
from qdrant_mcp.allure_indexer import _build_chunks, _extract_test_case_id
from qdrant_mcp.allure_qdrant_store import delete_test_cases, upsert_test_cases_batch
from qdrant_mcp.embedder import EmbedResponseError, embed_texts
from qdrant_mcp.sync_batch import get_flush_chunks
from qdrant_mcp.sync_state_store import (
    SyncState,
    delete_sync_state,
    list_sync_states,
    load_sync_states_dict,
    save_sync_states_batch,
)

logger = logging.getLogger(__name__)


@dataclass
class AllureSyncStats:
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: int = 0


DEFAULT_ALLURE_SYNC_MAX_WORKERS = 6


def _max_workers_from_env(env_name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env_name, str(default))))
    except ValueError:
        return default


def build_test_case_fingerprint(
    *,
    test_case: dict[str, Any],
    scenario: dict[str, Any],
    attachments: list[dict[str, Any]],
    tags: list[dict[str, Any]],
) -> str:
    payload = {
        "test_case": test_case,
        "scenario": scenario,
        "attachments": attachments,
        "tags": tags,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _state_id(source_id: str, test_case_id: int | str) -> str:
    return f"{source_id}:{test_case_id}"


def _prepare_test_case(source: Any, test_case_id: int, states_dict: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Fetches test case payload and builds chunks without embedding."""
    try:
        with AllureTestOpsClient() as client:
            payload = client.get_complete_test_case(test_case_id)
            fingerprint = build_test_case_fingerprint(
                test_case=payload.get("test_case", {}),
                scenario=payload.get("scenario", {}),
                attachments=payload.get("attachments", []),
                tags=payload.get("tags", []),
            )
            state_id = _state_id(source.id, test_case_id)
            previous = states_dict.get(state_id)
            if previous and previous.get("content_hash") == fingerprint:
                return {"status": "skipped", "test_case_id": str(test_case_id)}

            normalized = _build_chunks(test_case_id, payload, source.project_id)
            chunks = normalized["chunks"]
            if not chunks:
                return {
                    "status": "no_content",
                    "test_case_id": str(test_case_id),
                    "name": normalized["name"],
                }

            return {
                "status": "changed",
                "test_case_id": str(test_case_id),
                "name": normalized["name"],
                "metadata": normalized["metadata"],
                "chunks": chunks,
                "fingerprint": fingerprint,
            }
    except Exception as exc:
        return {"status": "error", "test_case_id": str(test_case_id), "message": str(exc)}


def sync_allure_source(source: Any, stale_after_minutes: int | None = None) -> dict[str, Any]:
    stats = AllureSyncStats()
    seen_ids: set[str] = set()
    with AllureTestOpsClient() as client:
        summaries = client.list_test_cases(project_id=source.project_id, rql=getattr(source, "rql", None))

    test_case_ids: list[int] = []
    for summary in summaries:
        test_case_id = _extract_test_case_id(summary)
        if test_case_id is None:
            stats.errors += 1
            continue
        seen_ids.add(str(test_case_id))
        test_case_ids.append(test_case_id)

    if not test_case_ids:
        return {
            "source_id": source.id,
            "project_id": source.project_id,
            "updated": 0,
            "skipped": 0,
            "deleted": 0,
            "errors": stats.errors,
        }

    max_workers = min(
        _max_workers_from_env("RAG_ALLURE_SYNC_MAX_WORKERS", DEFAULT_ALLURE_SYNC_MAX_WORKERS),
        len(test_case_ids) or 1,
    )

    states_dict = load_sync_states_dict("allure_test_case", f"{source.id}:")
    pending_changed: list[dict[str, Any]] = []
    pending_chunks = 0
    error_details: list[dict[str, str]] = []
    flush_threshold = get_flush_chunks()

    def flush_changed(items: list[dict[str, Any]]) -> None:
        nonlocal pending_changed, pending_chunks
        if not items:
            return

        all_names = [r["name"] for r in items]
        try:
            all_title_vectors_raw = embed_texts(all_names)
        except EmbedResponseError as exc:
            logger.error(
                "Failed to embed names for source %s: %s. Marking %d test cases as errors and continuing.",
                source.id, exc, len(items),
            )
            for r in items:
                stats.errors += 1
                error_details.append({"test_case_id": r["test_case_id"], "message": f"embedder failure: {exc}"})
            return

        batch_cases: list[dict[str, Any]] = []
        successful_items: list[dict[str, Any]] = []
        for i, r in enumerate(items):
            chunk_texts = [chunk["text"] for chunk in r["chunks"]]
            try:
                content_vectors = embed_texts(chunk_texts)
            except EmbedResponseError as exc:
                logger.error(
                    "Failed to embed test case %s for source %s: %s",
                    r["test_case_id"], source.id, exc,
                )
                stats.errors += 1
                error_details.append({"test_case_id": r["test_case_id"], "message": f"embedder failure: {exc}"})
                continue

            n = len(r["chunks"])
            batch_cases.append(
                {
                    "test_case_id": str(r["test_case_id"]),
                    "chunks": r["chunks"],
                    "content_vectors": content_vectors,
                    "title_vectors": [all_title_vectors_raw[i]] * n,
                    "metadata": r["metadata"],
                }
            )
            successful_items.append(r)

        if batch_cases:
            upsert_test_cases_batch(batch_cases)
            save_sync_states_batch(
                [
                    SyncState(
                        kind="allure_test_case",
                        source_id=_state_id(source.id, r["test_case_id"]),
                        content_hash=r["fingerprint"],
                        version="",
                        metadata={
                            "root_source_id": source.id,
                            "test_case_id": str(r["test_case_id"]),
                            "project_id": str(source.project_id),
                            "name": r["name"],
                        },
                    )
                    for r in successful_items
                ]
            )
            stats.updated += len(successful_items)

    if max_workers <= 1:
        for test_case_id in test_case_ids:
            r = _prepare_test_case(source, test_case_id, states_dict)
            status = r.get("status")
            if status == "skipped":
                stats.skipped += 1
            elif status == "error":
                stats.errors += 1
                error_details.append(
                    {
                        "test_case_id": str(r.get("test_case_id", "")),
                        "message": str(r.get("message", "unknown error")),
                    }
                )
            elif status == "changed":
                pending_changed.append(r)
                pending_chunks += len(r["chunks"])
                if pending_chunks >= flush_threshold:
                    flush_changed(pending_changed)
                    pending_changed = []
                    pending_chunks = 0
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-sync-allure") as executor:
            futures = {
                executor.submit(_prepare_test_case, source, test_case_id, states_dict): test_case_id
                for test_case_id in test_case_ids
            }
            for future in as_completed(futures):
                r = future.result()
                status = r.get("status")
                if status == "skipped":
                    stats.skipped += 1
                elif status == "error":
                    stats.errors += 1
                    error_details.append(
                        {
                            "test_case_id": str(r.get("test_case_id", "")),
                            "message": str(r.get("message", "unknown error")),
                        }
                    )
                elif status == "changed":
                    pending_changed.append(r)
                    pending_chunks += len(r["chunks"])
                    if pending_chunks >= flush_threshold:
                        flush_changed(pending_changed)
                        pending_changed = []
                        pending_chunks = 0

    if pending_changed:
        flush_changed(pending_changed)

    existing_states = list_sync_states("allure_test_case", f"{source.id}:")
    for state in existing_states:
        test_case_id = str(state.get("test_case_id") or str(state.get("source_id", "")).split(":", 1)[-1])
        if test_case_id and test_case_id not in seen_ids:
            delete_test_cases([test_case_id])
            delete_sync_state("allure_test_case", _state_id(source.id, test_case_id))
            stats.deleted += 1

    return {
        "source_id": source.id,
        "project_id": source.project_id,
        "updated": stats.updated,
        "skipped": stats.skipped,
        "deleted": stats.deleted,
        "errors": stats.errors,
        "error_details": error_details,
    }
