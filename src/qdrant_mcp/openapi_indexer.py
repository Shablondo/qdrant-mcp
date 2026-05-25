from __future__ import annotations

import json
from typing import Any

from qdrant_mcp.embedder import embed_texts
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
from qdrant_mcp.sync_state_store import (
    SyncState,
    delete_sync_state,
    list_sync_states,
    load_sync_states_dict,
    save_sync_states_batch,
)


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
    seen_keys: set[str] = set()
    changed_ops: list[dict[str, Any]] = []

    for operation in operations:
        operation_key = str(operation["operation_key"])
        seen_keys.add(operation_key)
        state_id = _state_id(source.id, operation_key)
        previous = states_dict.get(state_id)
        if not reindex and previous and previous.get("content_hash") == operation["operation_hash"]:
            skipped += 1
            continue
        changed_ops.append(
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

    if changed_ops:
        all_content_texts = [
            op["operation"].get("text") or operation_title(op["operation"])
            for op in changed_ops
        ]
        all_title_texts = [operation_title(op["operation"]) for op in changed_ops]
        all_content_vectors = embed_texts(all_content_texts)
        all_title_vectors = embed_texts(all_title_texts)

        upsert_operations_batch(
            operations=[op["operation"] for op in changed_ops],
            content_vectors=all_content_vectors,
            title_vectors=all_title_vectors,
            operation_keys=[op["operation_key"] for op in changed_ops],
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
                for op in changed_ops
            ]
        )

        updated = len(changed_ops)

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
        "errors": 0,
        "operations_total": len(operations),
        "spec_hash": fetched.spec_hash,
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
