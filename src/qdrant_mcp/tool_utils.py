"""Общие утилиты для MCP-инструментов."""

import json
import logging
from typing import Any, Callable, Dict

VALID_SEARCH_VECTORS = {"content", "title"}


def clamp_limit(limit: int, minimum: int = 1, maximum: int = 20) -> int:
    """Ограничивает числовой параметр заданным диапазоном."""
    return max(minimum, min(limit, maximum))


def normalize_search_vector(search_vector: str) -> str:
    """Возвращает допустимое имя вектора поиска."""
    return search_vector if search_vector in VALID_SEARCH_VECTORS else "content"


def normalize_string_list(value: Any) -> list[str] | None:
    """Принимает list[str] или JSON-string list от MCP UI и возвращает нормальный список."""
    if value is None:
        return None
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        if "," in stripped:
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return [stripped]
    return [str(value)]


def json_response(payload: Dict[str, Any]) -> str:
    """Сериализует успешный ответ инструмента."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def json_error(exc: Exception) -> str:
    """Сериализует ошибку инструмента."""
    return json.dumps({"error": str(exc)}, ensure_ascii=False)


def run_tool(logger: logging.Logger, tool_name: str, action: Callable[[], Dict[str, Any]]) -> str:
    """Выполняет действие инструмента с единообразным логированием и JSON-ответом."""
    try:
        return json_response(action())
    except Exception as exc:
        logger.error("[%s] Ошибка: %s", tool_name, exc, exc_info=True)
        return json_error(exc)
