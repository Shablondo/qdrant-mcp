from __future__ import annotations

import re


_ACTION_METHODS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "POST",
        (
            "добав",
            "созда",
            "привяз",
            "загруз",
            "импорт",
            "create",
            "add",
            "attach",
            "upload",
            "import",
        ),
    ),
    (
        "DELETE",
        (
            "удал",
            "отвяз",
            "delete",
            "remove",
            "detach",
        ),
    ),
    (
        "GET",
        (
            "получ",
            "найти",
            "поиск",
            "список",
            "просмотр",
            "get",
            "search",
            "find",
            "list",
        ),
    ),
    (
        "PUT/PATCH",
        (
            "обнов",
            "измен",
            "редакт",
            "замен",
            "update",
            "change",
            "edit",
            "replace",
        ),
    ),
)

_EXPLICIT_METHOD_RE = re.compile(r"\b(get|post|put|patch|delete)\b", re.IGNORECASE)
_META_SEARCH_MARKERS = (
    "curl",
    "crul",
    "endpoint",
    "эндпо",
    "api",
    "swagger",
    "openapi",
    "контракт",
    "метод",
    "операц",
    "запрос",
)


def infer_http_methods_from_query(query: str) -> list[str] | None:
    normalized = query.casefold()
    explicit_methods = sorted({match.group(1).upper() for match in _EXPLICIT_METHOD_RE.finditer(normalized)})
    if explicit_methods:
        return explicit_methods

    matched_groups: list[str] = []
    for method_group, markers in _ACTION_METHODS:
        if any(marker in normalized for marker in markers):
            matched_groups.append(method_group)

    if "GET" in matched_groups and len(matched_groups) > 1:
        if any(marker in normalized for marker in _META_SEARCH_MARKERS):
            matched_groups = [method_group for method_group in matched_groups if method_group != "GET"]

    if len(matched_groups) != 1:
        return None
    if matched_groups[0] == "PUT/PATCH":
        return ["PUT", "PATCH"]
    return [matched_groups[0]]


def infer_http_method_from_query(query: str) -> str | None:
    methods = infer_http_methods_from_query(query)
    if not methods or len(methods) != 1:
        return None
    return methods[0]
