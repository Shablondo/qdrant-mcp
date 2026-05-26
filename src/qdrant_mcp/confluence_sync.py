from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from qdrant_mcp.embedder import EmbedResponseError, embed_texts
from qdrant_mcp.confluence_utils import (
    _chunk_text,
    _fetch_child_pages,
    _fetch_page,
    _get_http_client,
    _html_to_text,
)
from qdrant_mcp.qdrant_store import delete_page, upsert_page_chunks_batch
from qdrant_mcp.sync_batch import get_flush_chunks
from qdrant_mcp.sync_state_store import SyncState, delete_sync_state, list_sync_states, load_sync_states_dict, save_sync_states_batch

import logging

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceSyncStats:
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: int = 0


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_changed(current: dict[str, Any], previous: dict[str, Any] | None) -> bool:
    if previous is None:
        return True
    return (
        current.get("version") != previous.get("version")
        or current.get("content_hash") != previous.get("content_hash")
    )


def _state_id(source_id: str, page_id: str) -> str:
    return f"{source_id}:{page_id}"


def sync_confluence_source(source: Any, stale_after_minutes: int | None = None) -> dict[str, Any]:
    stats = ConfluenceSyncStats()
    seen_page_ids: set[str] = set()
    visited: set[str] = set()
    root_page_id = str(source.root_page_id)
    pending: list[dict[str, Any]] = []
    pending_chunks = 0
    error_details: list[dict[str, str]] = []
    states_dict = load_sync_states_dict("confluence_page", f"{source.id}:")
    flush_threshold = get_flush_chunks()

    def flush_pending(items: list[dict[str, Any]]) -> None:
        if not items:
            return

        all_titles = [p["title"] for p in items]
        try:
            all_title_vectors_raw = embed_texts(all_titles)
        except EmbedResponseError as exc:
            logger.error(
                "Failed to embed titles for source %s: %s. Marking %d pages as errors and continuing.",
                source.id, exc, len(items),
            )
            for p in items:
                stats.errors += 1
                error_details.append({"page_id": p["page_id"], "message": f"embedder failure: {exc}"})
            return

        batch_pages: list[dict[str, Any]] = []
        successful_items: list[dict[str, Any]] = []
        for i, p in enumerate(items):
            try:
                content_vectors = embed_texts(p["chunks"])
            except EmbedResponseError as exc:
                logger.error(
                    "Failed to embed page %s for source %s: %s",
                    p["page_id"], source.id, exc,
                )
                stats.errors += 1
                error_details.append({"page_id": p["page_id"], "message": f"embedder failure: {exc}"})
                continue

            n = len(p["chunks"])
            batch_pages.append(
                {
                    "page_id": p["page_id"],
                    "chunks": p["chunks"],
                    "content_vectors": content_vectors,
                    "title_vectors": [all_title_vectors_raw[i]] * n,
                    "metadata": p["metadata"],
                }
            )
            successful_items.append(p)

        if batch_pages:
            upsert_page_chunks_batch(batch_pages)
            save_sync_states_batch(
                [
                    SyncState(
                        kind="confluence_page",
                        source_id=_state_id(source.id, p["page_id"]),
                        content_hash=p["current"]["content_hash"],
                        version=p["current"]["version"],
                        metadata={
                            "root_source_id": source.id,
                            "root_page_id": root_page_id,
                            "page_id": p["page_id"],
                            "title": p["title"],
                        },
                    )
                    for p in successful_items
                ]
            )
            stats.updated += len(successful_items)

    def walk(client: Any, page_id: str) -> None:
        nonlocal pending, pending_chunks

        if page_id in visited:
            return
        visited.add(page_id)
        seen_page_ids.add(page_id)

        page, fetch_error = _fetch_page(client, page_id)
        if page is None:
            stats.errors += 1
            error_details.append({"page_id": page_id, "message": fetch_error or "unknown error"})
        else:
            plain_text = _html_to_text(page.get("body_html", ""))
            current = {
                "version": str(page.get("version") or ""),
                "content_hash": content_hash(plain_text),
            }
            state_id = _state_id(source.id, page_id)
            previous = states_dict.get(state_id)
            if page_changed(current, previous):
                chunks = _chunk_text(plain_text, page.get("title", "")) if plain_text else []
                if chunks:
                    pending.append(
                        {
                            "page_id": page_id,
                            "chunks": chunks,
                            "title": page.get("title", ""),
                            "metadata": {
                                "title": page.get("title", ""),
                                "url": page.get("url", ""),
                                "space_key": page.get("space_key", getattr(source, "space_key", "") or ""),
                                "root_page_id": root_page_id,
                                "last_modified": page.get("last_modified", ""),
                            },
                            "current": current,
                        }
                    )
                    pending_chunks += len(chunks)
                    if pending_chunks >= flush_threshold:
                        flush_pending(pending)
                        pending = []
                        pending_chunks = 0
            else:
                stats.skipped += 1

        for child_id in _fetch_child_pages(client, page_id):
            walk(client, child_id)

    with _get_http_client() as client:
        walk(client, root_page_id)

    if pending:
        flush_pending(pending)

    existing_states = list_sync_states("confluence_page", f"{source.id}:")
    for state in existing_states:
        page_id = str(state.get("page_id") or str(state.get("source_id", "")).split(":", 1)[-1])
        if page_id and page_id not in seen_page_ids:
            delete_page(page_id)
            delete_sync_state("confluence_page", _state_id(source.id, page_id))
            stats.deleted += 1

    return {
        "source_id": source.id,
        "root_page_id": root_page_id,
        "updated": stats.updated,
        "skipped": stats.skipped,
        "deleted": stats.deleted,
        "errors": stats.errors,
        "error_details": error_details,
    }
