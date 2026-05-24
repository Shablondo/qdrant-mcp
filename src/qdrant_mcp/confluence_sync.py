from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from qdrant_mcp.embedder import embed_texts
from qdrant_mcp.indexer import (
    _chunk_text,
    _fetch_child_pages,
    _fetch_page,
    _get_http_client,
    _html_to_text,
)
from qdrant_mcp.qdrant_store import delete_page, upsert_page_chunks_batch
from qdrant_mcp.sync_state_store import SyncState, delete_sync_state, list_sync_states, load_sync_states_dict, save_sync_states_batch


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
    pages_to_index: list[dict[str, Any]] = []
    states_dict = load_sync_states_dict("confluence_page", f"{source.id}:")

    def walk(client: Any, page_id: str) -> None:
        if page_id in visited:
            return
        visited.add(page_id)
        seen_page_ids.add(page_id)

        page = _fetch_page(client, page_id)
        if not page:
            stats.errors += 1
            return

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
                pages_to_index.append(
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
            stats.updated += 1
        else:
            stats.skipped += 1

        for child_id in _fetch_child_pages(client, page_id):
            walk(client, child_id)

    with _get_http_client() as client:
        walk(client, root_page_id)

    if pages_to_index:
        all_chunk_texts: list[str] = []
        all_titles: list[str] = []
        for p in pages_to_index:
            all_chunk_texts.extend(p["chunks"])
            all_titles.append(p["title"])

        all_content_vectors = embed_texts(all_chunk_texts)
        all_title_vectors_raw = embed_texts(all_titles)

        batch_pages: list[dict[str, Any]] = []
        idx = 0
        for i, p in enumerate(pages_to_index):
            n = len(p["chunks"])
            batch_pages.append(
                {
                    "page_id": p["page_id"],
                    "chunks": p["chunks"],
                    "content_vectors": all_content_vectors[idx : idx + n],
                    "title_vectors": [all_title_vectors_raw[i]] * n,
                    "metadata": p["metadata"],
                }
            )
            idx += n

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
                for p in pages_to_index
            ]
        )

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
    }
