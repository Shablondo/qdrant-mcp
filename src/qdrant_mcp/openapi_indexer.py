from __future__ import annotations

import json
import logging
from typing import Any

from qdrant_mcp.embedder import EmbedResponseError, embed_texts
from qdrant_mcp.openapi_curl import build_curl_template
from qdrant_mcp.openapi_fetcher import fetch_openapi_spec
from qdrant_mcp.openapi_parser import normalize_openapi_operations
from qdrant_mcp.openapi_qdrant_store import (
    delete_operations,
    delete_operations_by_source,
    get_operation,
    operation_title,
    upsert_operations_batch,
)
from qdrant_mcp.sync_batch import get_flush_chunks
from qdrant_mcp.sync_state_store import (
    SyncState,
    delete_sync_state,
    list_sync_states,
    load_sync_states_dict,
    save_sync_states_batch,
)

logger = logging.getLogger(__name__)


def _state_id(source_id: str, operation_key: str) -> str:
    return f"{source_id}:{operation_key}"


def _operation_attachment(operation: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "openapi-operation",
        "service": operation.get("service"),
        "env": operation.get("env"),
        "method": operation.get("method"),
        "path": operation.get("path"),
        "operationId": operation.get("operation_id"),
        "summary": operation.get("summary"),
        "description": operation.get("description"),
        "parameters": operation.get("parameters", []),
        "requestBody": operation.get("request_body"),
        "responses": operation.get("responses", {}),
        "specUrl": operation.get("spec_url"),
        "swaggerUiUrl": operation.get("swagger_ui_url"),
        "specHash": operation.get("spec_hash"),
        "operationHash": operation.get("operation_hash"),
        "fetchedAt": operation.get("fetched_at"),
        "curlTemplate": build_curl_template(operation),
    }


def index_openapi_source(source: Any, *, reindex: bool = False) -> dict[str, Any]:
    fetched = fetch_openapi_spec(source)
    operations = normalize_openapi_operations(
        fetched.spec,
        source_id=source.id,
        service=source.service,
        env=source.env,
        spec_url=source.api_docs_url,
        request_base_url=source.request_base_url or source.api_docs_url.removesuffix("/v3/api-docs"),
        swagger_ui_url=getattr(source, "swagger_ui_url", None),
        spec_hash=fetched.spec_hash,
        fetched_at=fetched.fetched_at,
    )
    if reindex:
        delete_operations_by_source(source.id)

    states_dict = load_sync_states_dict("openapi_operation", f"{source.id}:")

    updated = 0
    skipped = 0
    errors_count = 0
    seen_keys: set[str] = set()
    pending_ops: list[dict[str, Any]] = []
    pending_chunks = 0
    error_details: list[dict[str, str]] = []
    flush_threshold = get_flush_chunks()

    def flush_operations(items: list[dict[str, Any]]) -> None:
        nonlocal updated, errors_count

        if not items:
            return

        successful_ops: list[dict[str, Any]] = []
        successful_content_vectors: list[list[float]] = []
        successful_title_vectors: list[list[float]] = []
        successful_items: list[dict[str, Any]] = []

        for op in items:
            content_text = op["operation"].get("text") or operation_title(op["operation"])
            title_text = operation_title(op["operation"])
            try:
                content_vecs = embed_texts([content_text])
                title_vecs = embed_texts([title_text])
            except EmbedResponseError as exc:
                logger.error(
                    "Failed to embed operation %s for source %s: %s",
                    op["operation_key"], source.id, exc,
                )
                errors_count += 1
                error_details.append({"operation_key": op["operation_key"], "message": f"embedder failure: {exc}"})
                continue

            successful_ops.append(op["operation"])
            successful_content_vectors.append(content_vecs[0] if content_vecs else [])
            successful_title_vectors.append(title_vecs[0] if title_vecs else [])
            successful_items.append(op)

        if successful_ops:
            upsert_operations_batch(
                operations=successful_ops,
                content_vectors=successful_content_vectors,
                title_vectors=successful_title_vectors,
                operation_keys=[op["operation_key"] for op in successful_items],
            )

            save_sync_states_batch(
                [
                    SyncState(
                        kind="openapi_operation",
                        source_id=op["state_id"],
                        content_hash=op["operation"]["operation_hash"],
                        version=op["spec_hash"],
                        metadata={
                            "root_source_id": source.id,
                            "operation_key": op["operation_key"],
                            "service": op["service"],
                            "method": op["method"],
                            "path": op["path"],
                            "spec_hash": op["spec_hash"],
                        },
                    )
                    for op in successful_items
                ]
            )

            updated += len(successful_items)

    for operation in operations:
        operation_key = str(operation["operation_key"])
        seen_keys.add(operation_key)
        state_id = _state_id(source.id, operation_key)
        previous = states_dict.get(state_id)
        if not reindex and previous and previous.get("content_hash") == operation["operation_hash"]:
            skipped += 1
            continue
        pending_ops.append(
            {
                "operation": operation,
                "operation_key": operation_key,
                "state_id": state_id,
                "spec_hash": fetched.spec_hash,
                "service": source.service,
                "method": operation["method"],
                "path": operation["path"],
            }
        )
        pending_chunks += 1  # one chunk per operation (text + title)
        if pending_chunks >= flush_threshold:
            flush_operations(pending_ops)
            pending_ops = []
            pending_chunks = 0

    if pending_ops:
        flush_operations(pending_ops)

    deleted = 0
    for state in list_sync_states("openapi_operation", f"{source.id}:"):
        operation_key = str(state.get("operation_key") or str(state.get("source_id", "")).split(":", 1)[-1])
        if operation_key and operation_key not in seen_keys:
            delete_operations([operation_key])
            delete_sync_state("openapi_operation", _state_id(source.id, operation_key))
            deleted += 1

    return {
        "source_id": source.id,
        "service": source.service,
        "env": source.env,
        "updated": updated,
        "skipped": skipped,
        "deleted": deleted,
        "errors": errors_count,
        "operations_total": len(operations),
        "spec_hash": fetched.spec_hash,
        "error_details": error_details,
    }


def build_openapi_attachment(service: str, method: str, path: str, *, format: str = "json") -> str:
    operation = get_operation(service, method, path)
    if not operation:
        return json.dumps(
            {"found": False, "service": service, "method": method.upper(), "path": path},
            ensure_ascii=False,
            indent=2,
        )
    attachment = _operation_attachment(operation)
    if format == "markdown":
        return (
            f"# {attachment['method']} {attachment['path']}\n\n"
            f"Service: {attachment['service']}\n\n"
            f"Summary: {attachment.get('summary') or ''}\n\n"
            "```bash\n"
            f"{attachment['curlTemplate']}\n"
            "```\n"
        )
    return json.dumps(attachment, ensure_ascii=False, indent=2)
