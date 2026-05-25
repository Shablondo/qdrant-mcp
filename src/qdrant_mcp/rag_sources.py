from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ConfluenceSource:
    id: str
    root_page_id: str
    space_key: str | None = None
    sync_interval_minutes: int = 1440


@dataclass(frozen=True)
class AllureSource:
    id: str
    project_id: int
    rql: str | None = None
    sync_interval_minutes: int = 1440


@dataclass(frozen=True)
class OpenApiSource:
    id: str
    service: str
    env: str
    api_docs_url: str
    request_base_url: str | None = None
    swagger_ui_url: str | None = None
    aliases: list[str] = field(default_factory=list)
    sync_interval_minutes: int = 1440

    def __post_init__(self) -> None:
        if self.swagger_ui_url:
            return
        base_url = (self.request_base_url or _base_url_from_api_docs_url(self.api_docs_url)).rstrip("/")
        object.__setattr__(self, "swagger_ui_url", f"{base_url}/swagger-ui/index.html#/")


@dataclass(frozen=True)
class RagSources:
    confluence: list[ConfluenceSource]
    allure: list[AllureSource]
    openapi: list[OpenApiSource]


def _base_url_from_api_docs_url(api_docs_url: str) -> str:
    for suffix in ("/v3/api-docs", "/v2/api-docs"):
        if api_docs_url.endswith(suffix):
            return api_docs_url.removesuffix(suffix)
    return api_docs_url.rsplit("/", 1)[0]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"RAG sources file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("RAG sources file must contain a YAML mapping")
    return data


def _items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"RAG sources section '{key}' must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"RAG sources section '{key}' must contain mappings")
    return value


def _validate_unique_ids(registry: RagSources) -> None:
    ids = [
        source.id
        for group in (registry.confluence, registry.allure, registry.openapi)
        for source in group
    ]
    duplicates = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate RAG source id: {', '.join(duplicates)}")


def load_rag_sources(path: str | Path) -> RagSources:
    data = _read_yaml(Path(path))
    registry = RagSources(
        confluence=[ConfluenceSource(**item) for item in _items(data, "confluence")],
        allure=[AllureSource(**item) for item in _items(data, "allure")],
        openapi=[OpenApiSource(**item) for item in _items(data, "openapi")],
    )
    _validate_unique_ids(registry)
    return registry
