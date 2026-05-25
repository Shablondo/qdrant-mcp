"""
confluence_tools.py — MCP инструменты для семантического поиска по Confluence.
"""

import logging
from typing import List, Optional

from qdrant_mcp.embedder import embed_single
from qdrant_mcp.qdrant_store import (
    discover_by_examples,
    ensure_collection_exists,
    get_collection_stats,
    get_page_chunks,
    list_indexed_pages,
    recommend_similar_pages,
    search as qdrant_search,
    search_hybrid,
)
from qdrant_mcp.server import mcp
from qdrant_mcp.tool_utils import clamp_limit, normalize_search_vector, run_tool

logger = logging.getLogger(__name__)


def _search(
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
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info(
        "[search] query=%r, limit=%s, root_page_id=%s, group_by=%s, search_vector=%s",
        query, limit, root_page_id, group_by, search_vector,
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


def _get_indexed_page(page_id: str) -> str:
    logger.info("[get_indexed_page] page_id=%s", page_id)

    def _action() -> dict:
        chunks = get_page_chunks(page_id)
        if not chunks:
            return {
                "page_id": page_id,
                "found": False,
                "message": f"Страница {page_id} не найдена в индексе.",
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


def _list_indexed() -> str:
    logger.info("[list_indexed]")
    return run_tool(
        logger,
        "list_indexed",
        lambda: {
            "total_pages": len(pages := list_indexed_pages()),
            "pages": pages,
        },
    )


def _get_collection_info() -> str:
    logger.info("[get_collection_info]")
    def _action() -> dict:
        ensure_collection_exists()
        return get_collection_stats()
    return run_tool(logger, "get_collection_info", _action)


def _find_similar_pages(
    page_id: str,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info("[find_similar_pages] page_id=%s, limit=%s, search_vector=%s", page_id, limit, search_vector)
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


def _search_by_examples(
    positive_page_ids: List[str],
    negative_page_ids: Optional[List[str]] = None,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info("[search_by_examples] positive=%s, negative=%s, limit=%s, search_vector=%s",
                positive_page_ids, negative_page_ids, limit, search_vector)
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


def _search_hybrid_tool(
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
    limit = clamp_limit(limit)
    search_vector = normalize_search_vector(search_vector)
    logger.info("[search_hybrid_tool] query=%r, limit=%s, search_vector=%s", query, limit, search_vector)

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
    return _search(query=query, limit=limit, root_page_id=root_page_id, space_key=space_key,
                   group_by=group_by, group_size=group_size, exclude_page_ids=exclude_page_ids,
                   search_vector=search_vector, last_modified_after=last_modified_after,
                   last_modified_before=last_modified_before, title_filter=title_filter,
                   context_size=context_size)


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
    return _search_hybrid_tool(query=query, limit=limit, root_page_id=root_page_id,
                               space_key=space_key, exclude_page_ids=exclude_page_ids,
                               search_vector=search_vector, last_modified_after=last_modified_after,
                               last_modified_before=last_modified_before, title_filter=title_filter)


@mcp.tool(name="rag_confluence_find_similar_pages")
def rag_confluence_find_similar_pages(
    page_id: str,
    limit: int = 5,
    space_key: Optional[str] = None,
    root_page_id: Optional[str] = None,
    exclude_page_ids: Optional[List[str]] = None,
    search_vector: str = "content",
) -> str:
    return _find_similar_pages(page_id=page_id, limit=limit, space_key=space_key,
                               root_page_id=root_page_id, exclude_page_ids=exclude_page_ids,
                               search_vector=search_vector)


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
    return _search_by_examples(positive_page_ids=positive_page_ids, negative_page_ids=negative_page_ids,
                               limit=limit, space_key=space_key, root_page_id=root_page_id,
                               exclude_page_ids=exclude_page_ids, search_vector=search_vector)


@mcp.tool(name="rag_confluence_get_indexed_page")
def rag_confluence_get_indexed_page(page_id: str) -> str:
    return _get_indexed_page(page_id)


@mcp.tool(name="rag_confluence_list_indexed_pages")
def rag_confluence_list_indexed_pages() -> str:
    return _list_indexed()


@mcp.tool(name="rag_confluence_get_collection_info")
def rag_confluence_get_collection_info() -> str:
    return _get_collection_info()
