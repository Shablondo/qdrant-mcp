from pathlib import Path

import pytest

from qdrant_mcp.rag_sources import load_rag_sources


def test_loads_registry_with_all_kinds(tmp_path: Path) -> None:
    config_path = tmp_path / "rag_sources.yaml"
    config_path.write_text(
        """
confluence:
  - id: docs
    root_page_id: "123"
    space_key: FUL
allure:
  - id: allure-38
    project_id: 38
openapi:
  - id: catalog
    service: fulfillment-catalog
    env: pp-test
    api_docs_url: https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/v3/api-docs
    request_base_url: https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru
    aliases: [sku, catalog]
""",
        encoding="utf-8",
    )

    registry = load_rag_sources(config_path)

    assert registry.confluence[0].id == "docs"
    assert registry.confluence[0].root_page_id == "123"
    assert registry.allure[0].project_id == 38
    assert registry.openapi[0].service == "fulfillment-catalog"
    assert registry.openapi[0].api_docs_url.endswith("/v3/api-docs")
    assert registry.openapi[0].request_base_url.endswith("5post-stage-5.salt.x5.ru")
    assert registry.openapi[0].swagger_ui_url == (
        "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/swagger-ui/index.html#/"
    )
    assert registry.openapi[0].aliases == ["sku", "catalog"]


def test_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "rag_sources.yaml"
    config_path.write_text(
        """
confluence:
  - id: duplicate
    root_page_id: "1"
openapi:
  - id: duplicate
    service: service
    env: pp-test
    api_docs_url: https://example.test/v3/api-docs
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate RAG source id"):
        load_rag_sources(config_path)
