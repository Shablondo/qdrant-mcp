from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)


logger = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://host.docker.internal:6333")
OPENAPI_QDRANT_COLLECTION = os.environ.get("OPENAPI_QDRANT_COLLECTION", "openapi_operations")
EMBED_DIMENSIONS = int(os.environ.get("EMBED_DIMENSIONS", "3072"))
_ENSURE_COLLECTION_LOCK = threading.Lock()


def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


def _is_collection_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message and OPENAPI_QDRANT_COLLECTION.lower() in message


def ensure_collection_exists() -> None:
    client = _client()
    with _ENSURE_COLLECTION_LOCK:
        existing = [collection.name for collection in client.get_collections().collections]
        if OPENAPI_QDRANT_COLLECTION in existing:
            return
        try:
            client.create_collection(
                collection_name=OPENAPI_QDRANT_COLLECTION,
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
            ("source_id", PayloadSchemaType.KEYWORD),
            ("service", PayloadSchemaType.KEYWORD),
            ("env", PayloadSchemaType.KEYWORD),
            ("method", PayloadSchemaType.KEYWORD),
            ("path", PayloadSchemaType.KEYWORD),
            ("operation_key", PayloadSchemaType.KEYWORD),
            ("operation_id", PayloadSchemaType.KEYWORD),
            ("tags", PayloadSchemaType.KEYWORD),
            ("summary", PayloadSchemaType.TEXT),
        ):
            client.create_payload_index(
                collection_name=OPENAPI_QDRANT_COLLECTION,
                field_name=field_name,
                field_schema=field_schema,
            )


def operation_title(operation: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            operation.get("service", ""),
            operation.get("method", ""),
            operation.get("path", ""),
            operation.get("summary", ""),
            operation.get("operation_id", ""),
        )
        if part
    )


def upsert_operation(
    operation: dict[str, Any],
    *,
    content_vector: list[float],
    title_vector: list[float],
) -> None:
    ensure_collection_exists()
    client = _client()
    delete_operations([str(operation["operation_key"])])
    client.upsert(
        collection_name=OPENAPI_QDRANT_COLLECTION,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector={"content": content_vector, "title": title_vector},
                payload=operation,
            )
        ],
    )


def delete_operations(operation_keys: list[str]) -> None:
    if not operation_keys:
        return
    ensure_collection_exists()
    client = _client()
    client.delete(
        collection_name=OPENAPI_QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="operation_key", match=MatchAny(any=operation_keys))]
        ),
    )


def delete_operations_by_source(source_id: str) -> None:
    ensure_collection_exists()
    client = _client()
    client.delete(
        collection_name=OPENAPI_QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
        ),
    )


def _operation_filter(
    *,
    service: str | None = None,
    method: str | None = None,
    methods: list[str] | None = None,
    path: str | None = None,
    source_id: str | None = None,
) -> Filter | None:
    conditions: list[FieldCondition] = []
    if service:
        conditions.append(FieldCondition(key="service", match=MatchValue(value=service)))
    if method:
        conditions.append(FieldCondition(key="method", match=MatchValue(value=method.upper())))
    elif methods:
        normalized_methods = [item.upper() for item in methods]
        conditions.append(FieldCondition(key="method", match=MatchAny(any=normalized_methods)))
    if path:
        conditions.append(FieldCondition(key="path", match=MatchValue(value=path)))
    if source_id:
        conditions.append(FieldCondition(key="source_id", match=MatchValue(value=source_id)))
    return Filter(must=conditions) if conditions else None


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "type",
        "format",
        "enum",
        "nullable",
        "description",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
    )
    compact = {key: schema[key] for key in keys if key in schema}
    items = schema.get("items")
    if isinstance(items, dict):
        compact["items"] = _compact_schema(items)
    nested_properties = schema.get("properties")
    if isinstance(nested_properties, dict):
        compact["properties_count"] = len(nested_properties)
    return compact


def _compact_parameters(parameters: Any) -> list[dict[str, Any]]:
    if not isinstance(parameters, list):
        return []
    compact_parameters: list[dict[str, Any]] = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        compact: dict[str, Any] = {
            "name": parameter.get("name"),
            "in": parameter.get("in"),
            "required": bool(parameter.get("required", False)),
        }
        if parameter.get("description"):
            compact["description"] = parameter["description"]
        schema = parameter.get("schema")
        if isinstance(schema, dict):
            compact["schema"] = _compact_schema(schema)
        compact_parameters.append(compact)
    return compact_parameters


def _compact_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return {
        "required": required,
        "properties": {
            name: _compact_schema(prop)
            for name, prop in properties.items()
            if isinstance(prop, dict)
        },
        "properties_count": len(properties),
    }


def _compact_request_body(request_body: Any) -> dict[str, Any] | None:
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
    compact: dict[str, Any] = {
        "required": bool(request_body.get("required", False)),
        "content_types": list(content.keys()),
    }
    json_content = content.get("application/json")
    if isinstance(json_content, dict) and isinstance(json_content.get("schema"), dict):
        compact["json"] = _compact_object_schema(json_content["schema"])
    multipart_content = content.get("multipart/form-data")
    if isinstance(multipart_content, dict) and isinstance(multipart_content.get("schema"), dict):
        compact["multipart_form"] = _compact_object_schema(multipart_content["schema"])
    return compact


def _response_codes(responses: Any) -> list[str]:
    if not isinstance(responses, dict):
        return []

    def sort_key(code: str) -> tuple[int, str]:
        return (int(code), code) if code.isdigit() else (999, code)

    return sorted((str(code) for code in responses), key=sort_key)


def compact_operation_result(operation: dict[str, Any], *, score: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if score is not None:
        result["score"] = round(score, 4)

    for field in (
        "source_id",
        "service",
        "env",
        "method",
        "path",
        "operation_id",
        "summary",
        "tags",
    ):
        if operation.get(field) is not None:
            result[field] = operation[field]

    result["parameters"] = _compact_parameters(operation.get("parameters"))
    result["request_body"] = _compact_request_body(operation.get("request_body"))

    codes = _response_codes(operation.get("responses"))
    result["response_codes"] = codes
    result["success_response_codes"] = [code for code in codes if code.isdigit() and 200 <= int(code) < 300]

    for field in (
        "request_base_url",
        "spec_url",
        "swagger_ui_url",
        "spec_hash",
        "operation_hash",
        "fetched_at",
    ):
        if operation.get(field) is not None:
            result[field] = operation[field]

    tool_args = {
        "service": operation.get("service"),
        "method": operation.get("method"),
        "path": operation.get("path"),
    }
    result["next_tools"] = {
        "curl": {
            "tool": "rag_openapi_build_curl_template",
            "args": tool_args,
        },
        "full_contract": {
            "tool": "rag_openapi_get_operation",
            "args": tool_args,
        },
    }
    return result


def search_operations(
    *,
    query_vector: list[float],
    limit: int = 5,
    service: str | None = None,
    method: str | None = None,
    methods: list[str] | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    ensure_collection_exists()
    client = _client()
    query_filter = _operation_filter(service=service, method=method, methods=methods, path=path)
    points = client.query_points(
        collection_name=OPENAPI_QDRANT_COLLECTION,
        query=query_vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
        using="content",
    ).points
    return [
        compact_operation_result(dict(point.payload or {}), score=getattr(point, "score", 0.0))
        for point in points
    ]


def get_operation(service: str, method: str, path: str) -> dict[str, Any] | None:
    ensure_collection_exists()
    client = _client()
    points, _ = client.scroll(
        collection_name=OPENAPI_QDRANT_COLLECTION,
        scroll_filter=_operation_filter(service=service, method=method, path=path),
        with_payload=True,
        with_vectors=False,
        limit=1,
    )
    if not points:
        return None
    return dict(points[0].payload or {})


def list_indexed_operations(service: str | None = None) -> list[dict[str, Any]]:
    ensure_collection_exists()
    client = _client()
    results: list[dict[str, Any]] = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=OPENAPI_QDRANT_COLLECTION,
            scroll_filter=_operation_filter(service=service),
            offset=offset,
            with_payload=True,
            with_vectors=False,
            limit=500,
        )
        results.extend(dict(point.payload or {}) for point in points)
        if next_offset is None:
            break
        offset = next_offset
    return sorted(results, key=lambda item: (item.get("service", ""), item.get("path", ""), item.get("method", "")))


def get_collection_stats() -> dict[str, Any]:
    ensure_collection_exists()
    info = _client().get_collection(collection_name=OPENAPI_QDRANT_COLLECTION)
    return {
        "collection": OPENAPI_QDRANT_COLLECTION,
        "points_count": info.points_count,
        "vectors_count": info.vectors_count,
        "status": str(info.status),
        "qdrant_url": QDRANT_URL,
    }
