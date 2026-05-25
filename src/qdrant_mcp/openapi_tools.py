"""
openapi_tools.py — MCP инструменты для семантического поиска по OpenAPI/Swagger.
"""

import logging
from typing import List, Optional

from qdrant_mcp.embedder import embed_single
from qdrant_mcp.openapi_curl import build_curl_template
from qdrant_mcp.openapi_indexer import build_openapi_attachment, index_openapi_source
from qdrant_mcp.openapi_intent import infer_http_methods_from_query
from qdrant_mcp.openapi_qdrant_store import (
    get_collection_stats as get_openapi_collection_stats,
    get_operation as get_openapi_operation_from_store,
    list_indexed_operations,
    search_operations as search_openapi_operations_qdrant,
)
from qdrant_mcp.rag_sync import load_registry, sync_sources
from qdrant_mcp.server import mcp
from qdrant_mcp.tool_utils import clamp_limit, normalize_string_list, run_tool

logger = logging.getLogger(__name__)


@mcp.tool(name="rag_openapi_index_sources")
def rag_openapi_index_sources(
    source_ids: Optional[List[str] | str] = None,
    sources_path: Optional[str] = None,
) -> str:
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
                "selected_operation": selected,
                "candidates_count": len(candidates),
                "candidates": candidates,
                "message": "Selected operation was found in search results but not by exact lookup.",
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
    logger.info("[rag_openapi_get_operation] service=%s method=%s path=%s", service, method, path)

    def _action() -> dict:
        operation = get_openapi_operation_from_store(service, method, path)
        return {"found": bool(operation), "operation": operation}

    return run_tool(logger, "rag_openapi_get_operation", _action)


@mcp.tool(name="rag_openapi_build_attachment")
def rag_openapi_build_attachment(service: str, method: str, path: str, format: str = "json") -> str:
    return build_openapi_attachment(service, method, path, format=format)


@mcp.tool(name="rag_openapi_build_curl_template")
def rag_openapi_build_curl_template(service: str, method: str, path: str) -> str:
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
    return run_tool(
        logger,
        "rag_openapi_list_indexed_operations",
        lambda: {"operations": list_indexed_operations(service), "service": service},
    )


@mcp.tool(name="rag_openapi_get_collection_info")
def rag_openapi_get_collection_info() -> str:
    return run_tool(logger, "rag_openapi_get_collection_info", get_openapi_collection_stats)
