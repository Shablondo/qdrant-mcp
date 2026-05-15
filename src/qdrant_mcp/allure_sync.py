from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any

from allure_client import AllureTestOpsClient
from allure_indexer import _extract_test_case_id, index_one_test_case
from allure_qdrant_store import delete_test_cases
from sync_state_store import SyncState, delete_sync_state, get_sync_state, list_sync_states, save_sync_state


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


def _sync_one_test_case(source: Any, test_case_id: int) -> dict[str, Any]:
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
            previous = get_sync_state("allure_test_case", state_id)
            if previous and previous.get("content_hash") == fingerprint:
                return {"status": "skipped", "test_case_id": str(test_case_id)}

            result = index_one_test_case(client, test_case_id, source.project_id, full_payload=payload)
            save_sync_state(
                SyncState(
                    kind="allure_test_case",
                    source_id=state_id,
                    content_hash=fingerprint,
                    version=str(payload.get("test_case", {}).get("updatedDate") or ""),
                    metadata={
                        "root_source_id": source.id,
                        "test_case_id": str(test_case_id),
                        "project_id": str(source.project_id),
                        "name": result.get("name", ""),
                    },
                )
            )
            return {"status": "updated", "test_case_id": str(test_case_id)}
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

    max_workers = min(
        _max_workers_from_env("RAG_ALLURE_SYNC_MAX_WORKERS", DEFAULT_ALLURE_SYNC_MAX_WORKERS),
        len(test_case_ids) or 1,
    )
    if max_workers <= 1:
        case_results = [_sync_one_test_case(source, test_case_id) for test_case_id in test_case_ids]
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-sync-allure") as executor:
            futures = [executor.submit(_sync_one_test_case, source, test_case_id) for test_case_id in test_case_ids]
            case_results = [future.result() for future in futures]

    for case_result in case_results:
        status = case_result.get("status")
        if status == "updated":
            stats.updated += 1
        elif status == "skipped":
            stats.skipped += 1
        else:
            stats.errors += 1

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
    }
