"""
qdrant_store.py — обёртка над qdrant-client для хранения и поиска
проиндексированных страниц Confluence.
"""

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
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "confluence_docs")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "3072"))
_ENSURE_COLLECTION_LOCK = threading.Lock()

UPSERT_BATCH_SIZE = 64
PAGE_SCROLL_LIMIT = 1000
LIST_SCROLL_LIMIT = 500
CONTEXT_SEPARATOR = "\n\n...[пропущено]...\n\n"


def _is_collection_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message and QDRANT_COLLECTION.lower() in message


def _serialize_point(point: Any) -> Dict[str, Any]:
    """Преобразует hit/point Qdrant в словарь результата."""
    payload = point.payload or {}
    return {
        "score": round(getattr(point, "score", 0.0), 4),
        "page_id": payload.get("page_id"),
        "title": payload.get("title"),
        "url": payload.get("url"),
        "space_key": payload.get("space_key"),
        "chunk_index": payload.get("chunk_index"),
        "text": payload.get("text"),
        "last_modified": payload.get("last_modified"),
    }


def _build_filter(
    *,
    page_id_filter: Optional[str] = None,
    space_key_filter: Optional[str] = None,
    root_page_id_filter: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> Optional[Filter]:
    """Собирает единый фильтр для поисковых операций."""
    must_conditions: List[FieldCondition] = []
    must_not_conditions: List[FieldCondition] = []

    if page_id_filter:
        must_conditions.append(FieldCondition(key="page_id", match=MatchValue(value=page_id_filter)))
    if space_key_filter:
        must_conditions.append(FieldCondition(key="space_key", match=MatchValue(value=space_key_filter)))
    if root_page_id_filter:
        must_conditions.append(FieldCondition(key="root_page_id", match=MatchValue(value=root_page_id_filter)))
    if last_modified_after:
        must_conditions.append(FieldCondition(key="last_modified", range=Range(gte=last_modified_after)))
    if last_modified_before:
        must_conditions.append(FieldCondition(key="last_modified", range=Range(lte=last_modified_before)))
    if title_filter:
        must_conditions.append(FieldCondition(key="title", match=MatchText(text=title_filter)))
    if exclude_page_ids:
        must_not_conditions.append(FieldCondition(key="page_id", match=MatchAny(any=exclude_page_ids)))

    if not must_conditions and not must_not_conditions:
        return None

    return Filter(must=must_conditions or None, must_not=must_not_conditions or None)


def _scroll_page_points(
    client: QdrantClient,
    page_id: str,
    *,
    with_vectors: bool,
    limit: int = PAGE_SCROLL_LIMIT,
) -> List[Any]:
    """Возвращает все точки конкретной страницы."""
    results, _ = client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="page_id", match=MatchValue(value=page_id))]
        ),
        with_payload=True,
        with_vectors=with_vectors,
        limit=limit,
    )
    return results


def _get_page_vector(client: QdrantClient, page_id: str, search_vector: str) -> Optional[List[float]]:
    """Достаёт любой вектор страницы для recommendation/discovery запросов."""
    results, _ = client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="page_id", match=MatchValue(value=page_id))]
        ),
        with_payload=False,
        with_vectors=True,
        limit=1,
    )
    if not results:
        return None

    vector = results[0].vector
    if isinstance(vector, dict):
        return vector.get(search_vector)
    return vector


def _extend_results_with_context(results: List[Dict[str, Any]], context_size: int) -> List[Dict[str, Any]]:
    """Добавляет соседние чанки к результатам поиска."""
    if context_size <= 0:
        return results

    chunks_by_page: Dict[str, List[Dict[str, Any]]] = {}
    index_lookup: Dict[str, Dict[int, int]] = {}

    for result in results:
        page_id = result.get("page_id")
        chunk_index = result.get("chunk_index")
        if not page_id or chunk_index is None:
            continue

        if page_id not in chunks_by_page:
            page_chunks = get_page_chunks(page_id)
            chunks_by_page[page_id] = page_chunks
            index_lookup[page_id] = {
                chunk["chunk_index"]: index for index, chunk in enumerate(page_chunks)
            }

        current_idx = index_lookup[page_id].get(chunk_index)
        if current_idx is None:
            continue

        page_chunks = chunks_by_page[page_id]
        start_idx = max(0, current_idx - context_size)
        end_idx = min(len(page_chunks), current_idx + context_size + 1)
        context_chunks = page_chunks[start_idx:end_idx]

        result["context"] = CONTEXT_SEPARATOR.join(
            f"[Чанк {chunk['chunk_index']}]: {chunk['text']}"
            for chunk in context_chunks
        )
        result["context_chunks_count"] = len(context_chunks)

    return results


def ensure_collection_exists() -> None:
    """
    Создаёт коллекцию Qdrant если она ещё не существует.
    Метрика Cosine, размерность EMBED_DIMENSIONS.
    Использует named vectors для заголовков и контента.
    """
    client = get_qdrant_client()
    with _ENSURE_COLLECTION_LOCK:
        existing = [collection.name for collection in client.get_collections().collections]

        if QDRANT_COLLECTION in existing:
            logger.debug("Коллекция '%s' уже существует", QDRANT_COLLECTION)
            return

        logger.info("Создание коллекции '%s' dim=%s", QDRANT_COLLECTION, EMBED_DIMENSIONS)
        try:
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config={
                    "content": VectorParams(size=EMBED_DIMENSIONS, distance=Distance.COSINE),
                    "title": VectorParams(size=EMBED_DIMENSIONS, distance=Distance.COSINE),
                },
            )
        except Exception as exc:
            if _is_collection_exists_error(exc):
                return
            raise

        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="page_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="root_page_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="space_key",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="last_modified",
            field_schema=PayloadSchemaType.DATETIME,
        )
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="title",
            field_schema=PayloadSchemaType.TEXT,
        )
        logger.info("Коллекция '%s' создана с named vectors и индексами payload", QDRANT_COLLECTION)



def upsert_page_chunks_batch(pages: list[dict[str, Any]]) -> int:
    client = get_qdrant_client()
    ensure_collection_exists()

    all_points: list[PointStruct] = []
    for page in pages:
        _delete_page_points(client, page["page_id"])
        for index, (chunk_text, content_vec, title_vec) in enumerate(
            zip(page["chunks"], page["content_vectors"], page["title_vectors"])
        ):
            all_points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"content": content_vec, "title": title_vec},
                    payload={
                        "page_id": page["page_id"],
                        "chunk_index": index,
                        "text": chunk_text,
                        **page["metadata"],
                    },
                )
            )

    total = 0
    for start in range(0, len(all_points), UPSERT_BATCH_SIZE):
        batch = all_points[start : start + UPSERT_BATCH_SIZE]
        client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
        total += len(batch)

    logger.info("Batch upsert: %s точек для %s страниц", total, len(pages))
    return total


def _delete_page_points(client: QdrantClient, page_id: str) -> None:
    """Удаляет все points для указанного page_id."""
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="page_id", match=MatchValue(value=page_id))]
        ),
    )
    logger.debug("Удалены старые points для page_id=%s", page_id)


def delete_page(page_id: str) -> None:
    """Удаляет все points для указанной страницы."""
    client = get_qdrant_client()
    _delete_page_points(client, page_id)


def delete_page_tree(root_page_id: str) -> None:
    """
    Удаляет все points для корневой страницы и всего её дерева.
    Использует поле root_page_id для фильтрации.
    """
    client = get_qdrant_client()
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="root_page_id", match=MatchValue(value=root_page_id))]
        ),
    )
    logger.info("Удалено дерево страниц с root_page_id=%s", root_page_id)


def search(
    query_vector: List[float],
    limit: int = 5,
    page_id_filter: Optional[str] = None,
    space_key_filter: Optional[str] = None,
    root_page_id_filter: Optional[str] = None,
    group_by: Optional[str] = None,
    group_size: int = 1,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
    context_size: int = 0,
) -> List[Dict[str, Any]]:
    """
    Семантический поиск по коллекции с поддержкой named vectors и расширения контекста.
    """
    client = get_qdrant_client()
    query_filter = _build_filter(
        page_id_filter=page_id_filter,
        space_key_filter=space_key_filter,
        root_page_id_filter=root_page_id_filter,
        exclude_page_ids=exclude_page_ids,
        last_modified_after=last_modified_after,
        last_modified_before=last_modified_before,
        title_filter=title_filter,
    )

    if group_by:
        groups_result = client.query_points_groups(
            collection_name=QDRANT_COLLECTION,
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
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            using=search_vector,
        ).points

    return _extend_results_with_context(
        [_serialize_point(result) for result in raw_results],
        context_size,
    )


def search_hybrid(
    query_vector: List[float],
    query_sparse_vector: Optional[Dict[str, Any]] = None,
    limit: int = 5,
    page_id_filter: Optional[str] = None,
    space_key_filter: Optional[str] = None,
    root_page_id_filter: Optional[str] = None,
    group_by: Optional[str] = None,
    group_size: int = 1,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Совместимый интерфейс для поиска без sparse-векторов.
    """
    client = get_qdrant_client()
    query_filter = _build_filter(
        page_id_filter=page_id_filter,
        space_key_filter=space_key_filter,
        root_page_id_filter=root_page_id_filter,
        exclude_page_ids=exclude_page_ids,
        last_modified_after=last_modified_after,
        last_modified_before=last_modified_before,
        title_filter=title_filter,
    )

    if group_by:
        logger.warning("group_by=%s игнорируется для hybrid search", group_by)

    if query_sparse_vector:
        logger.info("query_sparse_vector передан, но sparse search отключён; используется dense-only режим")

    raw_results = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
        using=search_vector,
    ).points

    return [_serialize_point(result) for result in raw_results]


def get_page_chunks(page_id: str) -> List[Dict[str, Any]]:
    """
    Возвращает все проиндексированные чанки страницы из Qdrant.
    """
    client = get_qdrant_client()
    results = _scroll_page_points(client, page_id, with_vectors=False)
    chunks = [
        {
            "chunk_index": payload.get("chunk_index", 0),
            "text": payload.get("text", ""),
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "last_modified": payload.get("last_modified", ""),
        }
        for result in results
        for payload in [result.payload or {}]
    ]
    return sorted(chunks, key=lambda item: item["chunk_index"])


def list_indexed_pages() -> List[Dict[str, Any]]:
    """
    Возвращает список уникальных проиндексированных страниц.
    """
    client = get_qdrant_client()
    seen_pages: Dict[str, Dict[str, Any]] = {}
    offset = None

    while True:
        results, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            offset=offset,
            with_payload=True,
            with_vectors=False,
            limit=LIST_SCROLL_LIMIT,
        )

        for result in results:
            payload = result.payload or {}
            page_id = payload.get("page_id")
            if page_id and page_id not in seen_pages:
                seen_pages[page_id] = {
                    "page_id": page_id,
                    "title": payload.get("title", ""),
                    "url": payload.get("url", ""),
                    "space_key": payload.get("space_key", ""),
                    "root_page_id": payload.get("root_page_id", ""),
                    "last_modified": payload.get("last_modified", ""),
                }

        if next_offset is None:
            break
        offset = next_offset

    return sorted(seen_pages.values(), key=lambda item: item["title"])


def get_collection_stats() -> Dict[str, Any]:
    """
    Возвращает статистику коллекции Qdrant.
    """
    client = get_qdrant_client()
    info = client.get_collection(collection_name=QDRANT_COLLECTION)
    return {
        "collection": QDRANT_COLLECTION,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": str(info.status),
        "qdrant_url": QDRANT_URL,
    }


def recommend_similar_pages(
    page_id: str,
    limit: int = 5,
    space_key_filter: Optional[str] = None,
    root_page_id_filter: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> List[Dict[str, Any]]:
    """
    Находит страницы, похожие на указанную (Recommendation API).
    """
    client = get_qdrant_client()
    positive_vector = _get_page_vector(client, page_id, search_vector)
    if positive_vector is None:
        logger.warning("Страница %s не найдена в индексе или не содержит вектор '%s'", page_id, search_vector)
        return []

    query_filter = _build_filter(
        space_key_filter=space_key_filter,
        root_page_id_filter=root_page_id_filter,
        exclude_page_ids=exclude_page_ids,
    )

    recommend_results = client.recommend(
        collection_name=QDRANT_COLLECTION,
        positive=[positive_vector],
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
        using=search_vector,
    )
    return [_serialize_point(result) for result in recommend_results]


def discover_by_examples(
    positive_page_ids: List[str],
    negative_page_ids: Optional[List[str]] = None,
    limit: int = 5,
    space_key_filter: Optional[str] = None,
    root_page_id_filter: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> List[Dict[str, Any]]:
    """
    Находит документы, похожие на набор примеров (Discovery API).
    """
    client = get_qdrant_client()

    positive_vectors = [
        vector
        for page_id in positive_page_ids
        for vector in [_get_page_vector(client, page_id, search_vector)]
        if vector is not None
    ]
    if not positive_vectors:
        logger.warning("Не удалось получить векторы для positive примеров")
        return []

    negative_vectors = [
        vector
        for page_id in negative_page_ids or []
        for vector in [_get_page_vector(client, page_id, search_vector)]
        if vector is not None
    ]

    query_filter = _build_filter(
        space_key_filter=space_key_filter,
        root_page_id_filter=root_page_id_filter,
        exclude_page_ids=exclude_page_ids,
    )

    discover_results = client.discover(
        collection_name=QDRANT_COLLECTION,
        target=positive_vectors,
        context=negative_vectors or None,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
        using=search_vector,
    )
    return [_serialize_point(result) for result in discover_results]
