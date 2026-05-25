"""
allure_tools.py — MCP инструменты для семантического поиска по Allure TestOps.
"""

import logging
from typing import List, Optional

from qdrant_mcp.allure_qdrant_store import (
    ensure_collection_exists as ensure_allure_collection_exists,
    get_collection_stats as get_allure_collection_stats,
    get_test_case_chunks,
    list_indexed_test_cases,
    search as search_allure_test_cases_qdrant,
)
from qdrant_mcp.embedder import embed_single
from qdrant_mcp.server import mcp
from qdrant_mcp.tool_utils import clamp_limit, normalize_search_vector, run_tool

logger = logging.getLogger(__name__)


def _search_allure_test_cases(
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
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info("[search_allure_test_cases] query=%r, limit=%s, project_id=%s, group_by=%s, search_vector=%s",
                query, limit, project_id, group_by, search_vector)

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


def _get_indexed_allure_test_case(test_case_id: str) -> str:
    logger.info("[get_indexed_allure_test_case] test_case_id=%s", test_case_id)

    def _action() -> dict:
        ensure_allure_collection_exists()
        chunks = get_test_case_chunks(test_case_id)
        if not chunks:
            return {
                "test_case_id": test_case_id,
                "found": False,
                "message": f"Тест-кейс {test_case_id} не найден в индексе.",
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


def _list_indexed_allure_test_cases() -> str:
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


def _get_allure_collection_info() -> str:
    logger.info("[get_allure_collection_info]")
    def _action() -> dict:
        ensure_allure_collection_exists()
        return get_allure_collection_stats()
    return run_tool(logger, "get_allure_collection_info", _action)


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
    return _search_allure_test_cases(
        query=query, limit=limit, project_id=project_id, status=status,
        owner=owner, tags=tags, chunk_types=chunk_types,
        exclude_test_case_ids=exclude_test_case_ids, group_by=group_by,
        group_size=group_size, search_vector=search_vector,
        updated_after=updated_after, updated_before=updated_before,
        name_filter=name_filter,
    )


@mcp.tool(name="rag_allure_get_indexed_test_case")
def rag_allure_get_indexed_test_case(test_case_id: str) -> str:
    return _get_indexed_allure_test_case(test_case_id)


@mcp.tool(name="rag_allure_list_indexed_test_cases")
def rag_allure_list_indexed_test_cases() -> str:
    return _list_indexed_allure_test_cases()


@mcp.tool(name="rag_allure_get_collection_info")
def rag_allure_get_collection_info() -> str:
    return _get_allure_collection_info()
