"""
allure_qdrant_store.py — хранение и поиск тест-кейсов Allure TestOps в Qdrant.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_mcp.qdrant_utils import get_qdrant_client
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchText,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

logger = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://host.docker.internal:6333")
ALLURE_QDRANT_COLLECTION = os.environ.get("ALLURE_QDRANT_COLLECTION", "allure_test_cases")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "2560"))
_ENSURE_COLLECTION_LOCK = threading.Lock()

UPSERT_BATCH_SIZE = int(os.environ.get("ALLURE_UPSERT_BATCH_SIZE", "64"))
LIST_SCROLL_LIMIT = int(os.environ.get("ALLURE_LIST_SCROLL_LIMIT", "500"))
TEST_CASE_SCROLL_LIMIT = int(os.environ.get("ALLURE_TEST_CASE_SCROLL_LIMIT", "1000"))


def _is_collection_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message and ALLURE_QDRANT_COLLECTION.lower() in message


def _serialize_point(point: Any) -> Dict[str, Any]:
    payload = point.payload or {}
    return {
        "score": round(getattr(point, "score", 0.0), 4),
        "test_case_id": payload.get("test_case_id"),
        "project_id": payload.get("project_id"),
        "name": payload.get("name"),
        "status": payload.get("status"),
        "owner": payload.get("owner"),
        "updated_at": payload.get("updated_at"),
        "tags": payload.get("tags", []),
        "chunk_type": payload.get("chunk_type"),
        "chunk_index": payload.get("chunk_index"),
        "text": payload.get("text"),
    }


def _build_filter(
    *,
    test_case_id_filter: Optional[str] = None,
    project_id_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    owner_filter: Optional[str] = None,
    tags_filter: Optional[List[str]] = None,
    chunk_types_filter: Optional[List[str]] = None,
    updated_after: Optional[str] = None,
    updated_before: Optional[str] = None,
    name_filter: Optional[str] = None,
    exclude_test_case_ids: Optional[List[str]] = None,
) -> Optional[Filter]:
    must_conditions: List[FieldCondition] = []
    must_not_conditions: List[FieldCondition] = []

    if test_case_id_filter:
        must_conditions.append(
            FieldCondition(key="test_case_id", match=MatchValue(value=test_case_id_filter))
        )
    if project_id_filter:
        must_conditions.append(
            FieldCondition(key="project_id", match=MatchValue(value=project_id_filter))
        )
    if status_filter:
        must_conditions.append(
            FieldCondition(key="status", match=MatchValue(value=status_filter))
        )
    if owner_filter:
        must_conditions.append(
            FieldCondition(key="owner", match=MatchValue(value=owner_filter))
        )
    if tags_filter:
        must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=tags_filter)))
    if chunk_types_filter:
        must_conditions.append(
            FieldCondition(key="chunk_type", match=MatchAny(any=chunk_types_filter))
        )
    if updated_after:
        must_conditions.append(FieldCondition(key="updated_at", range=Range(gte=updated_after)))
    if updated_before:
        must_conditions.append(FieldCondition(key="updated_at", range=Range(lte=updated_before)))
    if name_filter:
        must_conditions.append(FieldCondition(key="name", match=MatchText(text=name_filter)))
    if exclude_test_case_ids:
        must_not_conditions.append(
            FieldCondition(key="test_case_id", match=MatchAny(any=exclude_test_case_ids))
        )

    if not must_conditions and not must_not_conditions:
        return None

    return Filter(must=must_conditions or None, must_not=must_not_conditions or None)


def ensure_collection_exists() -> None:
    """Создаёт коллекцию тест-кейсов Allure при отсутствии."""
    client = get_qdrant_client()
    with _ENSURE_COLLECTION_LOCK:
        existing = [collection.name for collection in client.get_collections().collections]

        if ALLURE_QDRANT_COLLECTION in existing:
            return

        logger.info(
            "Создание коллекции '%s' для Allure TestOps dim=%s",
            ALLURE_QDRANT_COLLECTION,
            EMBED_DIMENSIONS,
        )
        try:
            client.create_collection(
                collection_name=ALLURE_QDRANT_COLLECTION,
                vectors_config={
                    "content": VectorParams(size=EMBED_DIMENSIONS, distance=Distance.COSINE),
                    "title": VectorParams(size=EMBED_DIMENSIONS, distance=Distance.COSINE),
                },
            )
        except Exception as exc:
            if _is_collection_exists_error(exc):
                return
            raise

        for field_name, field_schema in (
            ("test_case_id", PayloadSchemaType.KEYWORD),
            ("project_id", PayloadSchemaType.KEYWORD),
            ("status", PayloadSchemaType.KEYWORD),
            ("owner", PayloadSchemaType.KEYWORD),
            ("tags", PayloadSchemaType.KEYWORD),
            ("chunk_type", PayloadSchemaType.KEYWORD),
            ("updated_at", PayloadSchemaType.DATETIME),
            ("name", PayloadSchemaType.TEXT),
        ):
            client.create_payload_index(
                collection_name=ALLURE_QDRANT_COLLECTION,
                field_name=field_name,
                field_schema=field_schema,
            )



def upsert_test_cases_batch(test_cases: list[dict[str, Any]]) -> int:
    client = get_qdrant_client()
    ensure_collection_exists()

    all_test_case_ids = [tc["test_case_id"] for tc in test_cases]
    if all_test_case_ids:
        client.delete(
            collection_name=ALLURE_QDRANT_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="test_case_id", match=MatchAny(any=all_test_case_ids))]
            ),
        )

    all_points: list[PointStruct] = []
    for tc in test_cases:
        for index, (chunk, content_vec, title_vec) in enumerate(
            zip(tc["chunks"], tc["content_vectors"], tc["title_vectors"])
        ):
            all_points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"content": content_vec, "title": title_vec},
                    payload={
                        "test_case_id": tc["test_case_id"],
                        "chunk_index": index,
                        "chunk_type": chunk.get("chunk_type", "content"),
                        "text": chunk.get("text", ""),
                        **tc["metadata"],
                    },
                )
            )

    total = 0
    for start in range(0, len(all_points), UPSERT_BATCH_SIZE):
        batch = all_points[start : start + UPSERT_BATCH_SIZE]
        client.upsert(collection_name=ALLURE_QDRANT_COLLECTION, points=batch)
        total += len(batch)

    logger.info("Batch upsert: %s точек для %s тест-кейсов", total, len(test_cases))
    return total


def delete_test_cases(test_case_ids: List[str]) -> None:
    """Удаляет набор тест-кейсов из коллекции."""
    if not test_case_ids:
        return
    client = get_qdrant_client()
    client.delete(
        collection_name=ALLURE_QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="test_case_id", match=MatchAny(any=test_case_ids))]
        ),
    )


def search(
    query_vector: List[float],
    *,
    limit: int = 5,
    group_by: Optional[str] = "test_case_id",
    group_size: int = 2,
    project_id_filter: Optional[str] = None,
    test_case_id_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    owner_filter: Optional[str] = None,
    tags_filter: Optional[List[str]] = None,
    chunk_types_filter: Optional[List[str]] = None,
    updated_after: Optional[str] = None,
    updated_before: Optional[str] = None,
    name_filter: Optional[str] = None,
    exclude_test_case_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> List[Dict[str, Any]]:
    """Семантический поиск по индексу тест-кейсов Allure."""
    client = get_qdrant_client()
    query_filter = _build_filter(
        test_case_id_filter=test_case_id_filter,
        project_id_filter=project_id_filter,
        status_filter=status_filter,
        owner_filter=owner_filter,
        tags_filter=tags_filter,
        chunk_types_filter=chunk_types_filter,
        updated_after=updated_after,
        updated_before=updated_before,
        name_filter=name_filter,
        exclude_test_case_ids=exclude_test_case_ids,
    )

    if group_by:
        groups_result = client.query_points_groups(
            collection_name=ALLURE_QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            group_by=group_by,
            group_size=group_size,
            query_filter=query_filter,
            with_payload=True,
            using=search_vector,
        )
        raw_results = [point for group in groups_result.groups for point in group.hits]
    else:
        raw_results = client.query_points(
            collection_name=ALLURE_QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            using=search_vector,
        ).points

    return [_serialize_point(result) for result in raw_results]


def get_test_case_chunks(test_case_id: str) -> List[Dict[str, Any]]:
    """Возвращает все чанки тест-кейса из индекса."""
    client = get_qdrant_client()
    results, _ = client.scroll(
        collection_name=ALLURE_QDRANT_COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="test_case_id", match=MatchValue(value=test_case_id))]
        ),
        with_payload=True,
        with_vectors=False,
        limit=TEST_CASE_SCROLL_LIMIT,
    )

    chunks = [
        {
            "chunk_index": payload.get("chunk_index", 0),
            "chunk_type": payload.get("chunk_type", ""),
            "text": payload.get("text", ""),
            "name": payload.get("name", ""),
            "project_id": payload.get("project_id", ""),
            "status": payload.get("status", ""),
            "owner": payload.get("owner", ""),
            "updated_at": payload.get("updated_at", ""),
            "tags": payload.get("tags", []),
        }
        for result in results
        for payload in [result.payload or {}]
    ]
    return sorted(chunks, key=lambda item: item["chunk_index"])


def list_indexed_test_cases() -> List[Dict[str, Any]]:
    """Возвращает уникальные тест-кейсы из индекса."""
    client = get_qdrant_client()
    seen: Dict[str, Dict[str, Any]] = {}
    offset = None

    while True:
        results, next_offset = client.scroll(
            collection_name=ALLURE_QDRANT_COLLECTION,
            offset=offset,
            with_payload=True,
            with_vectors=False,
            limit=LIST_SCROLL_LIMIT,
        )

        for result in results:
            payload = result.payload or {}
            test_case_id = payload.get("test_case_id")
            if test_case_id and test_case_id not in seen:
                seen[test_case_id] = {
                    "test_case_id": test_case_id,
                    "project_id": payload.get("project_id", ""),
                    "name": payload.get("name", ""),
                    "status": payload.get("status", ""),
                    "owner": payload.get("owner", ""),
                    "updated_at": payload.get("updated_at", ""),
                    "tags": payload.get("tags", []),
                }

        if next_offset is None:
            break
        offset = next_offset

    return sorted(seen.values(), key=lambda item: item["name"])


def get_collection_stats() -> Dict[str, Any]:
    """Возвращает статистику коллекции тест-кейсов."""
    client = get_qdrant_client()
    info = client.get_collection(collection_name=ALLURE_QDRANT_COLLECTION)
    return {
        "collection": ALLURE_QDRANT_COLLECTION,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": str(info.status),
        "qdrant_url": QDRANT_URL,
    }
