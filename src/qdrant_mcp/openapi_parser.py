from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return {"$ref": ref}
    current: Any = spec
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return {"$ref": ref}
        current = current[part]
    return copy.deepcopy(current)


def _resolve_refs(value: Any, spec: dict[str, Any], seen: set[str] | None = None) -> Any:
    seen = seen or set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            if ref in seen:
                return {"$ref": ref}
            resolved = _resolve_ref(spec, ref)
            return _resolve_refs(resolved, spec, seen | {ref})
        return {key: _resolve_refs(item, spec, seen) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(item, spec, seen) for item in value]
    return value


def _operation_text(operation: dict[str, Any]) -> str:
    parts = [
        f"{operation['method']} {operation['path']}",
        operation.get("summary", ""),
        operation.get("description", ""),
        operation.get("operation_id", ""),
        " ".join(operation.get("tags", [])),
    ]
    for parameter in operation.get("parameters", []):
        parts.append(f"{parameter.get('in')} {parameter.get('name')} {parameter.get('description', '')}")
    content = operation.get("request_body", {}).get("content", {}) if isinstance(operation.get("request_body"), dict) else {}
    parts.extend(content.keys())
    return "\n".join(str(part) for part in parts if part)


def normalize_openapi_operations(
    spec: dict[str, Any],
    *,
    source_id: str,
    service: str,
    env: str,
    spec_url: str,
    request_base_url: str | None,
    swagger_ui_url: str | None,
    spec_hash: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    operations: list[dict[str, Any]] = []

    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters", [])
        for raw_method, operation_spec in sorted(path_item.items()):
            method = raw_method.lower()
            if method not in HTTP_METHODS or not isinstance(operation_spec, dict):
                continue
            parameters = []
            if isinstance(path_parameters, list):
                parameters.extend(path_parameters)
            if isinstance(operation_spec.get("parameters"), list):
                parameters.extend(operation_spec["parameters"])
            normalized = {
                "source_id": source_id,
                "service": service,
                "env": env,
                "operation_key": f"{service}:{method.upper()}:{path}",
                "method": method.upper(),
                "path": path,
                "operation_id": operation_spec.get("operationId", ""),
                "summary": operation_spec.get("summary", ""),
                "description": operation_spec.get("description", ""),
                "tags": operation_spec.get("tags", []),
                "parameters": _resolve_refs(parameters, spec),
                "request_body": _resolve_refs(operation_spec.get("requestBody"), spec),
                "responses": _resolve_refs(operation_spec.get("responses", {}), spec),
                "spec_title": info.get("title", ""),
                "spec_version": info.get("version", ""),
                "spec_url": spec_url,
                "request_base_url": request_base_url,
                "swagger_ui_url": swagger_ui_url,
                "spec_hash": spec_hash,
                "fetched_at": fetched_at,
            }
            normalized["text"] = _operation_text(normalized)
            normalized["operation_hash"] = stable_hash(
                {key: value for key, value in normalized.items() if key not in {"fetched_at", "operation_hash"}}
            )
            operations.append(normalized)
    return operations
