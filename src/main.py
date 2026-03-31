"""
main.py — FastMCP сервер для семантического поиска по Confluence и Allure TestOps через Qdrant.

Инструменты:
  - index_page_tree      — индексация страницы и всего её дерева
  - reindex_page_tree    — переиндексация (удалить старое + проиндексировать заново)
  - search               — семантический поиск по проиндексированным страницам
  - get_indexed_page     — получить содержимое страницы из Qdrant
  - list_indexed         — список всех проиндексированных страниц
  - get_collection_stats — статистика коллекции Qdrant
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
from tool_utils import clamp_limit, normalize_search_vector, run_tool

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
        "и тест-кейсам Allure TestOps, проиндексированным в локальной базе данных Qdrant. "
        "Используй 'search' для поиска релевантных страниц по теме. "
        "Используй 'index_page_tree' для первичной индексации раздела документации. "
        "Используй 'reindex_page_tree' для обновления документации после её изменений. "
        "Используй 'index_allure_test_cases' и 'search_allure_test_cases' для работы с тест-кейсами."
    ),
)

@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def get_collection_info() -> str:
    """
    Возвращает статистику коллекции Qdrant: количество точек, статус, URL.
    """
    logger.info("[get_collection_info]")

    def _action() -> dict:
        ensure_collection_exists()
        return get_collection_stats()

    return run_tool(logger, "get_collection_info", _action)


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def get_allure_collection_info() -> str:
    """
    Возвращает статистику коллекции тест-кейсов Allure в Qdrant.
    """
    logger.info("[get_allure_collection_info]")

    def _action() -> dict:
        ensure_allure_collection_exists()
        return get_allure_collection_stats()

    return run_tool(logger, "get_allure_collection_info", _action)


if __name__ == "__main__":
    logger.info("Запуск qdrant-mcp сервера...")
    mcp.run(transport="stdio")
