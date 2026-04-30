from __future__ import annotations

import json
import re
from typing import Any


_PATH_PARAMETER_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")


def _header_value(parameter: dict[str, Any]) -> str:
    if parameter.get("example") is not None:
        return str(parameter["example"])
    examples = parameter.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and first.get("value") is not None:
            return str(first["value"])
    name = str(parameter.get("name") or "Header")
    return f"{{{{{name}}}}}"


def _example_for_schema(name: str, schema: dict[str, Any] | None) -> Any:
    if not isinstance(schema, dict):
        return f"{{{{{name}}}}}"
    if schema.get("example") is not None:
        return schema["example"]
    if schema.get("default") is not None:
        return schema["default"]

    schema_type = schema.get("type")
    schema_format = schema.get("format")
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return False
    if schema_type == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        return [_example_for_schema(name.rstrip("s") or "item", item_schema)]
    if schema_type == "object" or isinstance(schema.get("properties"), dict):
        return _sample_json_body(schema)
    return f"{{{{{name}}}}}"


def _sample_json_body(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    selected_names = list(properties.keys())
    if required:
        selected_names = [name for name in selected_names if name in required] or selected_names
    return {
        name: _example_for_schema(name, properties.get(name) if isinstance(properties.get(name), dict) else {})
        for name in selected_names
    }


def _content(operation: dict[str, Any]) -> dict[str, Any]:
    request_body = operation.get("request_body")
    if not isinstance(request_body, dict):
        return {}
    content = request_body.get("content")
    return content if isinstance(content, dict) else {}


def _schema_for_content(operation: dict[str, Any], content_type: str) -> dict[str, Any] | None:
    body_content = _content(operation).get(content_type)
    if not isinstance(body_content, dict):
        return None
    schema = body_content.get("schema")
    return schema if isinstance(schema, dict) else None


def _form_lines(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    lines: list[str] = []
    for name, prop in properties.items():
        is_file = isinstance(prop, dict) and prop.get("type") == "string" and prop.get("format") == "binary"
        value = f'@"{{{{{name}Path}}}}"' if is_file else f"{{{{{name}}}}}"
        lines.append(f"--form '{name}={value}'")
    return lines


def _url(operation: dict[str, Any]) -> str:
    base_url = str(operation.get("request_base_url") or "").rstrip("/")
    path = str(operation.get("path") or "")
    if path.startswith("http://") or path.startswith("https://"):
        return _PATH_PARAMETER_RE.sub(r"{{\1}}", path)
    return _PATH_PARAMETER_RE.sub(r"{{\1}}", f"{base_url}{path}")


def build_curl_template(operation: dict[str, Any]) -> str:
    url = _url(operation)
    globoff = " --globoff" if "{{" in url or "[" in url or "]" in url else ""
    lines = [f"curl --location{globoff} '{url}'"]

    parameters = operation.get("parameters") if isinstance(operation.get("parameters"), list) else []
    header_names = set()
    for parameter in parameters:
        if not isinstance(parameter, dict) or parameter.get("in") != "header":
            continue
        name = str(parameter.get("name"))
        header_names.add(name.lower())
        lines.append(f"--header '{name}: {_header_value(parameter)}'")

    json_schema = _schema_for_content(operation, "application/json")
    if json_schema is not None:
        if "content-type" not in header_names:
            lines.append("--header 'Content-Type: application/json'")
        body = json.dumps(_sample_json_body(json_schema), ensure_ascii=False, indent=4)
        lines.append(f"--data '{body}'")

    multipart_schema = _schema_for_content(operation, "multipart/form-data")
    if multipart_schema is not None:
        lines.extend(_form_lines(multipart_schema))

    return " \\\n".join(lines)
