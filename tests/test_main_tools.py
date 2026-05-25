import json
from copy import deepcopy

from qdrant_mcp.main import mcp
import qdrant_mcp.openapi_tools as openapi_tools
import qdrant_mcp.sync_tools as sync_tools


def _get_tool(name):
    return mcp._tool_manager._tools[name]


def _get_tool_names():
    return set(mcp._tool_manager._tools.keys())


def _create_sku_operation() -> dict:
    return {
        "source_id": "fulfillment-catalog-pp-test",
        "service": "fulfillment-catalog",
        "env": "pp-test",
        "method": "POST",
        "path": "/api/v1/sku",
        "operation_id": "createSku",
        "summary": "Создание sku",
        "parameters": [],
        "request_body": {"content": {"application/json": {"schema": {"type": "object"}}}},
        "responses": {"201": {"description": "Успешное создание sku"}},
    }


def test_registers_explicit_rag_tool_names() -> None:
    tools = _get_tool_names()

    expected = {
        "rag_confluence_search",
        "rag_confluence_search_hybrid",
        "rag_confluence_find_similar_pages",
        "rag_confluence_search_by_examples",
        "rag_confluence_get_indexed_page",
        "rag_confluence_list_indexed_pages",
        "rag_confluence_get_collection_info",
        "rag_allure_search_test_cases",
        "rag_allure_get_indexed_test_case",
        "rag_allure_list_indexed_test_cases",
        "rag_allure_get_collection_info",
        "rag_openapi_search_operations",
        "rag_openapi_get_operation",
        "rag_openapi_build_attachment",
        "rag_openapi_build_curl_template",
        "rag_openapi_find_curl",
        "rag_openapi_index_sources",
        "rag_openapi_reindex_sources",
        "rag_openapi_list_indexed_operations",
        "rag_openapi_get_collection_info",
        "rag_sync_sources",
        "rag_list_sources",
        "rag_get_sync_status",
        "rag_get_source_sync_status",
    }

    assert expected <= tools


def test_does_not_register_legacy_tool_names() -> None:
    tools = _get_tool_names()
    legacy_tools = {
        "rag_confluence_index_page_tree",
        "rag_confluence_reindex_page_tree",
        "rag_allure_index_test_cases",
        "rag_allure_reindex_test_cases",
    }

    assert tools.isdisjoint(legacy_tools)


def test_openapi_search_tool_does_not_expose_include_details() -> None:
    params = _get_tool("rag_openapi_search_operations").parameters["properties"]

    assert "include_details" not in params


def test_sync_sources_tool_accepts_stringified_list_arguments() -> None:
    params = _get_tool("rag_sync_sources").parameters["properties"]

    for name in ("kinds", "source_ids"):
        allowed_types = {
            schema.get("type")
            for schema in params[name]["anyOf"]
            if isinstance(schema, dict)
        }
        assert {"array", "string", "null"} <= allowed_types
    assert params["stale_after_minutes"]["default"] == 1440


def test_sync_sources_parses_kilocode_stringified_arrays(monkeypatch) -> None:
    calls = {}

    monkeypatch.setattr(
        sync_tools,
        "sync_sources",
        lambda **kwargs: calls.update(kwargs) or {"totals": {}, "results": {}},
    )

    tool_fn = _get_tool("rag_sync_sources").fn
    response = json.loads(
        tool_fn(
            kinds='["openapi"]',
            source_ids='["fulfillment-shipment-pp-test"]',
            force=True,
        )
    )

    assert response == {"totals": {}, "results": {}}
    assert calls["kinds"] == ["openapi"]
    assert calls["source_ids"] == ["fulfillment-shipment-pp-test"]
    assert calls["force"] is True


def test_get_sync_status_tool_normalizes_blank_filters_and_passes_limit(monkeypatch) -> None:
    calls = {}

    monkeypatch.setattr(
        sync_tools,
        "get_sync_status",
        lambda **kwargs: calls.update(kwargs) or {"states": [], "states_count": 0},
    )

    tool_fn = _get_tool("rag_get_sync_status").fn
    response = json.loads(tool_fn(kind="", source_id_prefix="", limit=25))

    assert response == {"states": [], "states_count": 0}
    assert calls == {"kind": None, "source_id_prefix": None, "limit": 25}


def test_openapi_search_infers_http_method_from_action_query(monkeypatch) -> None:
    calls = {}

    def fake_search(**kwargs):
        calls.update(kwargs)
        return [{"service": "fulfillment-catalog", "method": "POST"}]

    monkeypatch.setattr(openapi_tools, "embed_single", lambda query: [0.1, 0.2, 0.3])
    monkeypatch.setattr(openapi_tools, "search_openapi_operations_qdrant", fake_search)

    tool_fn = _get_tool("rag_openapi_search_operations").fn
    response = json.loads(
        tool_fn(
            query="добавление штрихкода barcode sku catalog",
            service="fulfillment-catalog",
            limit=3,
        )
    )

    assert calls["method"] is None
    assert calls["methods"] == ["POST"]
    assert response["inferred_methods"] == ["POST"]
    assert response["results_format"] == "compact"


def test_openapi_find_curl_returns_single_curl_without_full_payload(monkeypatch) -> None:
    compact_candidate = {
        "service": "fulfillment-catalog",
        "method": "POST",
        "path": "/api/v1/sku",
        "operation_id": "createSku",
        "summary": "Создание sku",
        "score": 0.7082,
    }
    full_operation = _create_sku_operation()

    monkeypatch.setattr(openapi_tools, "embed_single", lambda query: [0.1, 0.2, 0.3])
    monkeypatch.setattr(openapi_tools, "search_openapi_operations_qdrant", lambda **kwargs: [deepcopy(compact_candidate)])
    monkeypatch.setattr(
        openapi_tools,
        "get_openapi_operation_from_store",
        lambda service, method, path: deepcopy(full_operation),
    )
    monkeypatch.setattr(
        openapi_tools,
        "build_curl_template",
        lambda operation: "curl --location 'https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/api/v1/sku'",
    )

    tool_fn = _get_tool("rag_openapi_find_curl").fn
    response = json.loads(tool_fn(query="сделай curl для создания товара", service="fulfillment-catalog"))

    assert response["found"] is True
    assert response["inferred_methods"] == ["POST"]
    assert response["selected_operation"] == compact_candidate
    assert response["curl"].startswith("curl --location")
    assert "operation" not in response
    assert "responses" not in json.dumps(response, ensure_ascii=False)
