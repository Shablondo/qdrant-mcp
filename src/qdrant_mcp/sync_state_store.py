from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import threading
from typing import Any
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams


QDRANT_URL = os.environ.get("QDRANT_URL", "http://host.docker.internal:6333")
SYNC_STATE_COLLECTION = os.environ.get("RAG_SYNC_STATE_COLLECTION", "rag_sync_state")
_ENSURE_COLLECTION_LOCK = threading.Lock()


@dataclass(frozen=True)
class SyncState:
    kind: str
    source_id: str
    content_hash: str
    version: str | None = None
    synced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


def _is_collection_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message and SYNC_STATE_COLLECTION.lower() in message


def _ensure_collection(client: QdrantClient | None = None) -> None:
    client = client or _client()
    with _ENSURE_COLLECTION_LOCK:
        existing = [collection.name for collection in client.get_collections().collections]
        if SYNC_STATE_COLLECTION in existing:
            return
        try:
            client.create_collection(
                collection_name=SYNC_STATE_COLLECTION,
                vectors_config=VectorParams(size=1, distance=Distance.COSINE),
            )
        except Exception as exc:
            if _is_collection_exists_error(exc):
                return
            raise


def _filter(kind: str, source_id: str) -> Filter:
    return Filter(
        must=[
            FieldCondition(key="kind", match=MatchValue(value=kind)),
            FieldCondition(key="source_id", match=MatchValue(value=source_id)),
        ]
    )


def get_sync_state(kind: str, source_id: str) -> dict[str, Any] | None:
    client = _client()
    _ensure_collection(client)
    points, _ = client.scroll(
        collection_name=SYNC_STATE_COLLECTION,
        scroll_filter=_filter(kind, source_id),
        with_payload=True,
        with_vectors=False,
        limit=1,
    )
    if not points:
        return None
    return dict(points[0].payload or {})


def save_sync_state(state: SyncState) -> None:
    client = _client()
    _ensure_collection(client)
    delete_sync_state(state.kind, state.source_id)
    payload = {
        "kind": state.kind,
        "source_id": state.source_id,
        "content_hash": state.content_hash,
        "version": state.version,
        "synced_at": state.synced_at,
        **state.metadata,
    }
    client.upsert(
        collection_name=SYNC_STATE_COLLECTION,
        points=[PointStruct(id=str(uuid.uuid4()), vector=[0.0], payload=payload)],
    )


def save_sync_states_batch(states: list[SyncState]) -> None:
    if not states:
        return
    client = _client()
    _ensure_collection(client)
    for state in states:
        client.delete(
            collection_name=SYNC_STATE_COLLECTION,
            points_selector=_filter(state.kind, state.source_id),
        )
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0],
            payload={
                "kind": state.kind,
                "source_id": state.source_id,
                "content_hash": state.content_hash,
                "version": state.version,
                "synced_at": state.synced_at,
                **state.metadata,
            },
        )
        for state in states
    ]
    client.upsert(
        collection_name=SYNC_STATE_COLLECTION,
        points=points,
    )


def delete_sync_state(kind: str, source_id: str) -> None:
    client = _client()
    _ensure_collection(client)
    client.delete(collection_name=SYNC_STATE_COLLECTION, points_selector=_filter(kind, source_id))


def list_sync_states(
    kind: str | None = None,
    source_id_prefix: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    client = _client()
    _ensure_collection(client)
    must = []
    if kind:
        must.append(FieldCondition(key="kind", match=MatchValue(value=kind)))
    query_filter = Filter(must=must or None) if must else None
    states: list[dict[str, Any]] = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=SYNC_STATE_COLLECTION,
            scroll_filter=query_filter,
            offset=offset,
            with_payload=True,
            with_vectors=False,
            limit=500,
        )
        for point in points:
            payload = dict(point.payload or {})
            if source_id_prefix and not str(payload.get("source_id", "")).startswith(source_id_prefix):
                continue
            states.append(payload)
            if limit is not None and len(states) >= limit:
                return states
        if next_offset is None:
            break
        offset = next_offset
    return states
