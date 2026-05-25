"""
main.py — FastMCP сервер для семантического поиска по Confluence и Allure TestOps через Qdrant.

Публичные MCP-инструменты используют явные префиксы:
  - rag_confluence_* — документация Confluence
  - rag_allure_*     — тест-кейсы Allure TestOps
  - rag_openapi_*    — OpenAPI/Swagger контракты
  - rag_sync_*       — синхронизация RAG sources
"""

import logging
from typing import List, Optional

from fastmcp import FastMCP

from allure_indexer import run_index as run_allure_index
from allure_qdrant_store import (
    ensure_collection_exists as ensure_allure_collection_exists,
    get_collection_stats as get_allure_collection_stats,
    get_test_case_chunks,
    list_indexed_test_cases,
    search as search_allure_test_cases_qdrant,
)
from embedder import embed_single
from indexer import run_index as run_confluence_index
from openapi_curl import build_curl_template
from openapi_indexer import build_openapi_attachment, index_openapi_source
from openapi_intent import infer_http_methods_from_query
from openapi_qdrant_store import (
    get_collection_stats as get_openapi_collection_stats,
    get_operation as get_openapi_operation_from_store,
    list_indexed_operations,
    search_operations as search_openapi_operations_qdrant,
)
from qdrant_store import (
    delete_page_tree,
    discover_by_examples,
    ensure_collection_exists,
    get_collection_stats,
    get_page_chunks,
    list_indexed_pages,
    recommend_similar_pages,
    search as qdrant_search,
    search_hybrid,
)
from rag_sync import get_source_sync_status, get_sync_status, list_sources, load_registry, sync_sources
from tool_utils import clamp_limit, normalize_search_vector, normalize_string_list, run_tool

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Создаём FastMCP сервер
mcp = FastMCP(
    name="qdrant-mcp",
    instructions=(
        "Этот сервер предоставляет семантический поиск по документации Confluence "
        "тест-кейсам Allure TestOps и OpenAPI/Swagger контрактам, проиндексированным "
        "в локальной базе данных Qdrant. Используй явные инструменты "
        "'rag_confluence_*', 'rag_allure_*', 'rag_openapi_*' и 'rag_sync_*'."
    ),
)

def index_page_tree(page_id: str) -> str:
    """
    Индексирует страницу Confluence и все её дочерние страницы рекурсивно в Qdrant.

    Используй этот инструмент для первичной индексации раздела документации.
    Страницы разбиваются на смысловые чанки, которые векторизуются и сохраняются
    в локальный Qdrant для быстрого семантического поиска.

    Args:
        page_id: ID корневой страницы Confluence (число из URL страницы,
                 например '1392589758' из .../pages/1392589758/...).

    Returns:
        JSON-строка со статистикой: количество проиндексированных страниц и чанков.
    """
    logger.info("[index_page_tree] page_id=%s", page_id)
    return run_tool(logger, "index_page_tree", lambda: run_confluence_index(page_id))


def reindex_page_tree(page_id: str) -> str:
    """
    Переиндексирует дерево страниц Confluence.

    Сначала удаляет все ранее проиндексированные данные для этого дерева,
    затем выполняет полную повторную индексацию.
    Используй после изменений в документации Confluence.

    Args:
        page_id: ID корневой страницы Confluence.

    Returns:
        JSON-строка со статистикой переиндексации.
    """
    logger.info("[reindex_page_tree] page_id=%s", page_id)

    def _action() -> dict:
        logger.info("Удаление старых данных для дерева %s", page_id)
        delete_page_tree(root_page_id=page_id)
        result = run_confluence_index(page_id)
        result["action"] = "reindex"
        return result

    return run_tool(logger, "reindex_page_tree", _action)


def search(
    query: str,
    limit: int = 5,
    root_page_id: Optional[str] = None,
    space_key: Optional[str] = None,
    group_by: Optional[str] = None,
    group_size: int = 1,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
    context_size: int = 0,
) -> str:
    """
    Семантический поиск по проиндексированной документации Confluence.

    Находит наиболее релевантные фрагменты документации по смыслу запроса.
    Возвращает топ-N чанков с оценкой релевантности, заголовком страницы и URL.
    """
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[search] query=%r, limit=%s, root_page_id=%s, group_by=%s, search_vector=%s",
        query,
        limit,
        root_page_id,
        group_by,
        search_vector,
    )

    def _action() -> dict:
        results = qdrant_search(
            query_vector=embed_single(query),
            limit=limit,
            root_page_id_filter=root_page_id,
            space_key_filter=space_key,
            group_by=group_by,
            group_size=group_size,
            exclude_page_ids=exclude_page_ids,
            search_vector=search_vector,
            last_modified_after=last_modified_after,
            last_modified_before=last_modified_before,
            title_filter=title_filter,
            context_size=context_size,
        )
        return {
            "query": query,
            "search_vector": search_vector,
            "results_count": len(results),
            "results": results,
        }

    return run_tool(logger, "search", _action)


def get_indexed_page(page_id: str) -> str:
    """
    Возвращает все проиндексированные фрагменты страницы из Qdrant.
    """
    logger.info("[get_indexed_page] page_id=%s", page_id)

    def _action() -> dict:
        chunks = get_page_chunks(page_id)
        if not chunks:
            return {
                "page_id": page_id,
                "found": False,
                "message": f"Страница {page_id} не найдена в индексе. Сначала выполни index_page_tree.",
            }

        return {
            "page_id": page_id,
            "found": True,
            "title": chunks[0]["title"],
            "url": chunks[0]["url"],
            "chunks_count": len(chunks),
            "chunks": chunks,
        }

    return run_tool(logger, "get_indexed_page", _action)


def list_indexed() -> str:
    """
    Возвращает список всех проиндексированных страниц Confluence в Qdrant.
    """
    logger.info("[list_indexed]")
    return run_tool(
        logger,
        "list_indexed",
        lambda: {
            "total_pages": len(pages := list_indexed_pages()),
            "pages": pages,
        },
    )


def get_collection_info() -> str:
    """
    Возвращает статистику коллекции Qdrant: количество точек, статус, URL.
    """
    logger.info("[get_collection_info]")

    def _action() -> dict:
        ensure_collection_exists()
        return get_collection_stats()

    return run_tool(logger, "get_collection_info", _action)


def find_similar_pages(
    page_id: str,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    """
    Находит страницы, похожие на указанную (Recommendation API).
    """
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[find_similar_pages] page_id=%s, limit=%s, search_vector=%s",
        page_id,
        limit,
        search_vector,
    )

    return run_tool(
        logger,
        "find_similar_pages",
        lambda: {
            "page_id": page_id,
            "search_vector": search_vector,
            "results_count": len(
                results := recommend_similar_pages(
                    page_id=page_id,
                    limit=limit,
                    space_key_filter=space_key,
                    root_page_id_filter=root_page_id,
                    exclude_page_ids=exclude_page_ids,
                    search_vector=search_vector,
                )
            ),
            "results": results,
        },
    )


def search_by_examples(
    positive_page_ids: List[str],
    negative_page_ids: Optional[List[str]] = None,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    """
    Находит документы, похожие на набор примеров (Discovery API).
    """
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[search_by_examples] positive=%s, negative=%s, limit=%s, search_vector=%s",
        positive_page_ids,
        negative_page_ids,
        limit,
        search_vector,
    )

    return run_tool(
        logger,
        "search_by_examples",
        lambda: {
            "positive_page_ids": positive_page_ids,
            "negative_page_ids": negative_page_ids,
            "search_vector": search_vector,
            "results_count": len(
                results := discover_by_examples(
                    positive_page_ids=positive_page_ids,
                    negative_page_ids=negative_page_ids,
                    limit=limit,
                    space_key_filter=space_key,
                    root_page_id_filter=root_page_id,
                    exclude_page_ids=exclude_page_ids,
                    search_vector=search_vector,
                )
            ),
            "results": results,
        },
    )


def search_hybrid_tool(
    query: str,
    limit: int = 5,
    root_page_id: Optional[str] = None,
    space_key: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> str:
    """
    Совместимый dense-only поиск вместо прежнего hybrid search.
    """
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[search_hybrid_tool] query=%r, limit=%s, search_vector=%s",
        query,
        limit,
        search_vector,
    )

    def _action() -> dict:
        results = search_hybrid(
            query_vector=embed_single(query),
            limit=limit,
            root_page_id_filter=root_page_id,
            space_key_filter=space_key,
            exclude_page_ids=exclude_page_ids,
            search_vector=search_vector,
            last_modified_after=last_modified_after,
            last_modified_before=last_modified_before,
            title_filter=title_filter,
        )
        return {
            "query": query,
            "search_type": "dense_only",
            "search_vector": search_vector,
            "results_count": len(results),
            "results": results,
        }

    return run_tool(logger, "search_hybrid_tool", _action)


def index_allure_test_cases(
    project_id: Optional[int] = None,
    rql: Optional[str] = None,
    page_size: int = 100,
    max_test_cases: Optional[int] = None,
) -> str:
    """
    Индексирует тест-кейсы Allure TestOps в отдельную коллекцию Qdrant.

    Для каждого тест-кейса получает полный контекст по workflow:
    test case -> scenario -> attachments -> tags.

    Args:
        project_id: ID проекта Allure TestOps. Если не указан, используется ALLURE_TESTOPS_PROJECT_ID из переменных окружения.
        rql: RQL запрос для фильтрации тест-кейсов.
             Строковые значения заключать в кавычки: tag="fulfillment", status="Active"
             Числовые значения без кавычек: id=12345, projectId=38
             Примеры: tag="smoke", status="Active" and owner="Nikita.Shablinsky"
        page_size: Количество тест-кейсов на страницу при пагинации (по умолчанию 100).
        max_test_cases: Максимальное количество тест-кейсов для индексации (опционально).
    """
    logger.info(
        "[index_allure_test_cases] project_id=%s, rql=%s, page_size=%s, max_test_cases=%s",
        project_id,
        rql,
        page_size,
        max_test_cases,
    )
    return run_tool(
        logger,
        "index_allure_test_cases",
        lambda: run_allure_index(
            project_id=project_id,
            rql=rql,
            page_size=page_size,
            max_test_cases=max_test_cases,
            reindex=False,
        ),
    )


def reindex_allure_test_cases(
    project_id: Optional[int] = None,
    rql: Optional[str] = None,
    page_size: int = 100,
    max_test_cases: Optional[int] = None,
) -> str:
    """
    Переиндексирует тест-кейсы Allure TestOps.

    Если `rql` не указан, очищает индекс проекта целиком.
    Если `rql` указан, переиндексирует только совпавшие тест-кейсы.

    Args:
        project_id: ID проекта Allure TestOps. Если не указан, используется ALLURE_TESTOPS_PROJECT_ID из переменных окружения.
        rql: RQL запрос для фильтрации тест-кейсов.
             Строковые значения заключать в кавычки: tag="fulfillment", status="Active"
             Числовые значения без кавычек: id=12345, projectId=38
             Примеры: tag="smoke", status="Active" and owner="Nikita.Shablinsky"
        page_size: Количество тест-кейсов на страницу при пагинации (по умолчанию 100).
        max_test_cases: Максимальное количество тест-кейсов для индексации (опционально).
    """
    logger.info(
        "[reindex_allure_test_cases] project_id=%s, rql=%s, page_size=%s, max_test_cases=%s",
        project_id,
        rql,
        page_size,
        max_test_cases,
    )
    return run_tool(
        logger,
        "reindex_allure_test_cases",
        lambda: run_allure_index(
            project_id=project_id,
            rql=rql,
            page_size=page_size,
            max_test_cases=max_test_cases,
            reindex=True,
        ),
    )


def search_allure_test_cases(
    query: str,
    limit: int = 5,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    tags: Optional[List[str]] = None,
    chunk_types: Optional[List[str]] = None,
    exclude_test_case_ids: Optional[List[str]] = None,
    group_by: Optional[str] = "test_case_id",
    group_size: int = 2,
    search_vector: str = "content",
    updated_after: Optional[str] = None,
    updated_before: Optional[str] = None,
    name_filter: Optional[str] = None,
) -> str:
    """
    Семантический поиск по проиндексированным тест-кейсам Allure TestOps.

    По умолчанию результаты группируются по `test_case_id`, чтобы вернуть несколько
    разных тест-кейсов вместо множества чанков одного кейса.
    """
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[search_allure_test_cases] query=%r, limit=%s, project_id=%s, group_by=%s, search_vector=%s",
        query,
        limit,
        project_id,
        group_by,
        search_vector,
    )

    def _action() -> dict:
        ensure_allure_collection_exists()
        results = search_allure_test_cases_qdrant(
            query_vector=embed_single(query),
            limit=limit,
            group_by=group_by,
            group_size=group_size,
            project_id_filter=project_id,
            status_filter=status,
            owner_filter=owner,
            tags_filter=tags,
            chunk_types_filter=chunk_types,
            updated_after=updated_after,
            updated_before=updated_before,
            name_filter=name_filter,
            exclude_test_case_ids=exclude_test_case_ids,
            search_vector=search_vector,
        )
        return {
            "query": query,
            "search_vector": search_vector,
            "group_by": group_by,
            "results_count": len(results),
            "results": results,
        }

    return run_tool(logger, "search_allure_test_cases", _action)


def get_indexed_allure_test_case(test_case_id: str) -> str:
    """
    Возвращает все проиндексированные чанки тест-кейса Allure из Qdrant.
    """
    logger.info("[get_indexed_allure_test_case] test_case_id=%s", test_case_id)

    def _action() -> dict:
        ensure_allure_collection_exists()
        chunks = get_test_case_chunks(test_case_id)
        if not chunks:
            return {
                "test_case_id": test_case_id,
                "found": False,
                "message": (
                    f"Тест-кейс {test_case_id} не найден в индексе. "
                    "Сначала выполни index_allure_test_cases."
                ),
            }

        return {
            "test_case_id": test_case_id,
            "found": True,
            "name": chunks[0]["name"],
            "project_id": chunks[0]["project_id"],
            "status": chunks[0]["status"],
            "owner": chunks[0]["owner"],
            "updated_at": chunks[0]["updated_at"],
            "tags": chunks[0]["tags"],
            "chunks_count": len(chunks),
            "chunks": chunks,
        }

    return run_tool(logger, "get_indexed_allure_test_case", _action)


def list_indexed_allure_test_cases() -> str:
    """
    Возвращает список всех проиндексированных тест-кейсов Allure.
    """
    logger.info("[list_indexed_allure_test_cases]")
    ensure_allure_collection_exists()
    return run_tool(
        logger,
        "list_indexed_allure_test_cases",
        lambda: {
            "total_test_cases": len(test_cases := list_indexed_test_cases()),
            "test_cases": test_cases,
        },
    )


def get_allure_collection_info() -> str:
    """
    Возвращает статистику коллекции тест-кейсов Allure в Qdrant.
    """
    logger.info("[get_allure_collection_info]")

    def _action() -> dict:
        ensure_allure_collection_exists()
        return get_allure_collection_stats()

    return run_tool(logger, "get_allure_collection_info", _action)


@mcp.tool(name="rag_confluence_index_page_tree")
def rag_confluence_index_page_tree(page_id: str) -> str:
    """Индексирует страницу Confluence и всех её потомков рекурсивно."""
    return index_page_tree(page_id)


@mcp.tool(name="rag_confluence_reindex_page_tree")
def rag_confluence_reindex_page_tree(page_id: str) -> str:
    """Переиндексирует страницу Confluence и всех её потомков рекурсивно."""
    return reindex_page_tree(page_id)


@mcp.tool(name="rag_confluence_search")
def rag_confluence_search(
    query: str,
    limit: int = 5,
    root_page_id: Optional[str] = None,
    space_key: Optional[str] = None,
    group_by: Optional[str] = None,
    group_size: int = 1,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
    context_size: int = 0,
) -> str:
    """Семантический поиск по Confluence RAG."""
    return search(
        query=query,
        limit=limit,
        root_page_id=root_page_id,
        space_key=space_key,
        group_by=group_by,
        group_size=group_size,
        exclude_page_ids=exclude_page_ids,
        search_vector=search_vector,
        last_modified_after=last_modified_after,
        last_modified_before=last_modified_before,
        title_filter=title_filter,
        context_size=context_size,
    )


@mcp.tool(name="rag_confluence_search_hybrid")
def rag_confluence_search_hybrid(
    query: str,
    limit: int = 5,
    root_page_id: Optional[str] = None,
    space_key: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
    last_modified_after: Optional[str] = None,
    last_modified_before: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> str:
    """Совместимый dense-only поиск по Confluence RAG."""
    return search_hybrid_tool(
        query=query,
        limit=limit,
        root_page_id=root_page_id,
        space_key=space_key,
        exclude_page_ids=exclude_page_ids,
        search_vector=search_vector,
        last_modified_after=last_modified_after,
        last_modified_before=last_modified_before,
        title_filter=title_filter,
    )


@mcp.tool(name="rag_confluence_find_similar_pages")
def rag_confluence_find_similar_pages(
    page_id: str,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    """Находит похожие страницы Confluence."""
    return find_similar_pages(
        page_id=page_id,
        limit=limit,
        space_key=space_key,
        root_page_id=root_page_id,
        exclude_page_ids=exclude_page_ids,
        search_vector=search_vector,
    )


@mcp.tool(name="rag_confluence_search_by_examples")
def rag_confluence_search_by_examples(
    positive_page_ids: List[str],
    negative_page_ids: Optional[List[str]] = None,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    """Находит страницы Confluence по positive/negative examples."""
    return search_by_examples(
        positive_page_ids=positive_page_ids,
        negative_page_ids=negative_page_ids,
        limit=limit,
        space_key=space_key,
        root_page_id=root_page_id,
        exclude_page_ids=exclude_page_ids,
        search_vector=search_vector,
    )


@mcp.tool(name="rag_confluence_get_indexed_page")
def rag_confluence_get_indexed_page(page_id: str) -> str:
    """Возвращает индексированные чанки страницы Confluence."""
    return get_indexed_page(page_id)


@mcp.tool(name="rag_confluence_list_indexed_pages")
def rag_confluence_list_indexed_pages() -> str:
    """Возвращает список индексированных страниц Confluence."""
    return list_indexed()


@mcp.tool(name="rag_confluence_get_collection_info")
def rag_confluence_get_collection_info() -> str:
    """Возвращает статистику коллекции Confluence."""
    return get_collection_info()


@mcp.tool(name="rag_allure_index_test_cases")
def rag_allure_index_test_cases(
    project_id: Optional[int] = None,
    rql: Optional[str] = None,
    page_size: int = 100,
    max_test_cases: Optional[int] = None,
) -> str:
    """Индексирует тест-кейсы Allure TestOps."""
    return index_allure_test_cases(project_id=project_id, rql=rql, page_size=page_size, max_test_cases=max_test_cases)


@mcp.tool(name="rag_allure_reindex_test_cases")
def rag_allure_reindex_test_cases(
    project_id: Optional[int] = None,
    rql: Optional[str] = None,
    page_size: int = 100,
    max_test_cases: Optional[int] = None,
) -> str:
    """Переиндексирует тест-кейсы Allure TestOps."""
    return reindex_allure_test_cases(project_id=project_id, rql=rql, page_size=page_size, max_test_cases=max_test_cases)


@mcp.tool(name="rag_allure_search_test_cases")
def rag_allure_search_test_cases(
    query: str,
    limit: int = 5,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    tags: Optional[List[str]] = None,
    chunk_types: Optional[List[str]] = None,
    exclude_test_case_ids: Optional[List[str]] = None,
    group_by: Optional[str] = "test_case_id",
    group_size: int = 2,
    search_vector: str = "content",
    updated_after: Optional[str] = None,
    updated_before: Optional[str] = None,
    name_filter: Optional[str] = None,
) -> str:
    """Семантический поиск по Allure TestOps RAG."""
    return search_allure_test_cases(
        query=query,
        limit=limit,
        project_id=project_id,
        status=status,
        owner=owner,
        tags=tags,
        chunk_types=chunk_types,
        exclude_test_case_ids=exclude_test_case_ids,
        group_by=group_by,
        group_size=group_size,
        search_vector=search_vector,
        updated_after=updated_after,
        updated_before=updated_before,
        name_filter=name_filter,
    )


@mcp.tool(name="rag_allure_get_indexed_test_case")
def rag_allure_get_indexed_test_case(test_case_id: str) -> str:
    """Возвращает индексированные чанки тест-кейса Allure."""
    return get_indexed_allure_test_case(test_case_id)


@mcp.tool(name="rag_allure_list_indexed_test_cases")
def rag_allure_list_indexed_test_cases() -> str:
    """Возвращает список индексированных тест-кейсов Allure."""
    return list_indexed_allure_test_cases()


@mcp.tool(name="rag_allure_get_collection_info")
def rag_allure_get_collection_info() -> str:
    """Возвращает статистику коллекции Allure."""
    return get_allure_collection_info()


@mcp.tool(name="rag_openapi_index_sources")
def rag_openapi_index_sources(
    source_ids: Optional[List[str] | str] = None,
    sources_path: Optional[str] = None,
) -> str:
    """Инкрементально индексирует OpenAPI sources из registry."""
    return run_tool(
        logger,
        "rag_openapi_index_sources",
        lambda: sync_sources(kinds=["openapi"], source_ids=normalize_string_list(source_ids), sources_path=sources_path),
    )


@mcp.tool(name="rag_openapi_reindex_sources")
def rag_openapi_reindex_sources(
    source_ids: Optional[List[str] | str] = None,
    sources_path: Optional[str] = None,
) -> str:
    """Принудительно переиндексирует OpenAPI sources из registry."""
    logger.info("[rag_openapi_reindex_sources] source_ids=%s", source_ids)

    def _action() -> dict:
        registry = load_registry(sources_path)
        requested_ids = set(normalize_string_list(source_ids) or [])
        results = []
        for source in registry.openapi:
            if requested_ids and source.id not in requested_ids:
                continue
            results.append(index_openapi_source(source, reindex=True))
        return {"results": results}

    return run_tool(logger, "rag_openapi_reindex_sources", _action)


@mcp.tool(name="rag_openapi_search_operations")
def rag_openapi_search_operations(
    query: str,
    limit: int = 5,
    service: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    """Семантический поиск по OpenAPI operations. Всегда возвращает компактные кандидаты."""
    limit = clamp_limit(limit)
    logger.info("[rag_openapi_search_operations] query=%r, service=%s, method=%s, path=%s", query, service, method, path)

    def _action() -> dict:
        inferred_methods = None if method else infer_http_methods_from_query(query)
        results = search_openapi_operations_qdrant(
            query_vector=embed_single(query),
            limit=limit,
            service=service,
            method=method.upper() if method else None,
            methods=inferred_methods,
            path=path,
        )
        return {
            "query": query,
            "results_count": len(results),
            "requested_method": method.upper() if method else None,
            "inferred_methods": inferred_methods,
            "results_format": "compact",
            "usage": (
                "Search returns candidate operations. For a final curl use "
                "rag_openapi_find_curl for query-based curl generation or "
                "rag_openapi_build_curl_template with service/method/path from the chosen result. "
                "For the full OpenAPI contract use rag_openapi_get_operation."
            ),
            "results": results,
        }

    return run_tool(logger, "rag_openapi_search_operations", _action)


@mcp.tool(name="rag_openapi_find_curl")
def rag_openapi_find_curl(
    query: str,
    service: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    limit: int = 3,
) -> str:
    """Находит лучшую OpenAPI operation по запросу и возвращает один curl-шаблон."""
    limit = clamp_limit(limit)
    logger.info("[rag_openapi_find_curl] query=%r, service=%s, method=%s, path=%s", query, service, method, path)

    def _action() -> dict:
        inferred_methods = None if method else infer_http_methods_from_query(query)
        candidates = search_openapi_operations_qdrant(
            query_vector=embed_single(query),
            limit=limit,
            service=service,
            method=method.upper() if method else None,
            methods=inferred_methods,
            path=path,
        )
        if not candidates:
            return {
                "found": False,
                "query": query,
                "service": service,
                "requested_method": method.upper() if method else None,
                "inferred_methods": inferred_methods,
                "candidates_count": 0,
                "candidates": [],
            }

        selected = candidates[0]
        selected_service = str(selected.get("service") or service or "")
        selected_method = str(selected.get("method") or method or "").upper()
        selected_path = str(selected.get("path") or path or "")
        operation = get_openapi_operation_from_store(selected_service, selected_method, selected_path)
        if not operation:
            return {
                "found": False,
                "query": query,
                "service": service,
                "requested_method": method.upper() if method else None,
                "inferred_methods": inferred_methods,
                "selected_operation": selected,
                "candidates_count": len(candidates),
                "candidates": candidates,
                "message": "Selected operation was found in search results but not found by exact service/method/path lookup.",
            }

        return {
            "found": True,
            "query": query,
            "service": selected_service,
            "requested_method": method.upper() if method else None,
            "inferred_methods": inferred_methods,
            "selected_operation": selected,
            "candidates_count": len(candidates),
            "candidates": candidates,
            "curl": build_curl_template(operation),
        }

    return run_tool(logger, "rag_openapi_find_curl", _action)


@mcp.tool(name="rag_openapi_get_operation")
def rag_openapi_get_operation(service: str, method: str, path: str) -> str:
    """Возвращает точную OpenAPI operation."""
    logger.info("[rag_openapi_get_operation] service=%s method=%s path=%s", service, method, path)

    def _action() -> dict:
        operation = get_openapi_operation_from_store(service, method, path)
        return {"found": bool(operation), "operation": operation}

    return run_tool(logger, "rag_openapi_get_operation", _action)


@mcp.tool(name="rag_openapi_build_attachment")
def rag_openapi_build_attachment(service: str, method: str, path: str, format: str = "json") -> str:
    """Формирует вложение OpenAPI operation с curlTemplate."""
    return build_openapi_attachment(service, method, path, format=format)


@mcp.tool(name="rag_openapi_build_curl_template")
def rag_openapi_build_curl_template(service: str, method: str, path: str) -> str:
    """Возвращает curl-шаблон для OpenAPI operation."""
    logger.info("[rag_openapi_build_curl_template] service=%s method=%s path=%s", service, method, path)

    def _action() -> dict:
        operation = get_openapi_operation_from_store(service, method, path)
        if not operation:
            return {"found": False, "service": service, "method": method.upper(), "path": path}
        return {
            "found": True,
            "service": service,
            "method": method.upper(),
            "path": path,
            "curl": build_curl_template(operation),
        }

    return run_tool(logger, "rag_openapi_build_curl_template", _action)


@mcp.tool(name="rag_openapi_list_indexed_operations")
def rag_openapi_list_indexed_operations(service: Optional[str] = None) -> str:
    """Возвращает список индексированных OpenAPI operations."""
    return run_tool(
        logger,
        "rag_openapi_list_indexed_operations",
        lambda: {"operations": list_indexed_operations(service), "service": service},
    )


@mcp.tool(name="rag_openapi_get_collection_info")
def rag_openapi_get_collection_info() -> str:
    """Возвращает статистику коллекции OpenAPI."""
    return run_tool(logger, "rag_openapi_get_collection_info", get_openapi_collection_stats)


@mcp.tool(name="rag_sync_sources")
def rag_sync_sources(
    kinds: Optional[List[str] | str] = None,
    source_ids: Optional[List[str] | str] = None,
    stale_after_minutes: Optional[int] = 1440,
    sources_path: Optional[str] = None,
    force: bool = False,
) -> str:
    """Инкрементально синхронизирует Confluence, Allure и OpenAPI sources."""
    return run_tool(
        logger,
        "rag_sync_sources",
        lambda: sync_sources(
            kinds=normalize_string_list(kinds),
            source_ids=normalize_string_list(source_ids),
            stale_after_minutes=stale_after_minutes,
            sources_path=sources_path,
            force=force,
        ),
    )


@mcp.tool(name="rag_list_sources")
def rag_list_sources(sources_path: Optional[str] = None) -> str:
    """Возвращает source registry."""
    return run_tool(logger, "rag_list_sources", lambda: list_sources(sources_path=sources_path))


@mcp.tool(name="rag_get_sync_status")
def rag_get_sync_status(
    kind: Optional[str] = None,
    source_id_prefix: Optional[str] = None,
    limit: int = 50,
) -> str:
    """Возвращает sync state из Qdrant. Требует kind и/или source_id_prefix, чтобы не выгружать всю коллекцию."""
    return run_tool(
        logger,
        "rag_get_sync_status",
        lambda: get_sync_status(
            kind=kind.strip() or None if isinstance(kind, str) else kind,
            source_id_prefix=source_id_prefix.strip() or None if isinstance(source_id_prefix, str) else source_id_prefix,
            limit=limit,
        ),
    )


@mcp.tool(name="rag_get_source_sync_status")
def rag_get_source_sync_status(sources_path: Optional[str] = None) -> str:
    """Возвращает per-source freshness status: last_checked_at, last_synced_at, next_due_at, due."""
    return run_tool(
        logger,
        "rag_get_source_sync_status",
        lambda: get_source_sync_status(sources_path=sources_path),
    )


if __name__ == "__main__":
    logger.info("Запуск qdrant-mcp сервера...")
    mcp.run(transport="stdio")
