"""
sync_tools.py — MCP инструменты для синхронизации RAG sources.
"""

import logging
from typing import List, Optional

from qdrant_mcp.rag_sync import get_source_sync_status, get_sync_status, list_sources, sync_sources
from qdrant_mcp.server import mcp
from qdrant_mcp.tool_utils import normalize_string_list, run_tool

logger = logging.getLogger(__name__)


@mcp.tool(name="rag_sync_sources")
def rag_sync_sources(
    kinds: Optional[List[str] | str] = None,
    source_ids: Optional[List[str] | str] = None,
    stale_after_minutes: Optional[int] = 1440,
    sources_path: Optional[str] = None,
    force: bool = False,
) -> str:
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
    return run_tool(logger, "rag_list_sources", lambda: list_sources(sources_path=sources_path))


@mcp.tool(name="rag_get_sync_status")
def rag_get_sync_status(
    kind: Optional[str] = None,
    source_id_prefix: Optional[str] = None,
    limit: int = 50,
) -> str:
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
    return run_tool(
        logger,
        "rag_get_source_sync_status",
        lambda: get_source_sync_status(sources_path=sources_path),
    )
