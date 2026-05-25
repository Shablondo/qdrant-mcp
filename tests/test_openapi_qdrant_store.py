import inspect
from types import SimpleNamespace

from qdrant_mcp.openapi_qdrant_store import compact_operation_result, search_operations


def _operation() -> dict:
    return {
        "source_id": "fulfillment-catalog-pp-test",
        "service": "fulfillment-catalog",
        "env": "pp-test",
        "operation_key": "fulfillment-catalog:POST:/api/v1/sku/{skuId}/barcodes",
        "method": "POST",
        "path": "/api/v1/sku/{skuId}/barcodes",
        "operation_id": "addBarcodeToSku",
        "summary": "Привязка нового штрихкода к существующему товару",
        "tags": ["Сервис операций с sku (v1)"],
        "parameters": [
            {
                "name": "Partner-Id",
                "in": "header",
                "description": "Идентификатор партнера",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
            },
            {
                "name": "skuId",
                "in": "path",
                "description": "Идентификатор sku",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
            },
        ],
        "request_body": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": "Запрос на создание штрихкода sku",
                        "properties": {
                            "value": {
                                "type": "string",
                                "maxLength": 20,
                                "minLength": 8,
                                "pattern": "[A-Z0-9\\-.$/+%() ]*",
                            },
                            "type": {"type": "string", "enum": ["PARTNER", "WMS"]},
                        },
                        "required": ["type", "value"],
                    }
                }
            },
            "required": True,
        },
        "responses": {
            "201": {"description": "Успешное создание штрихкода для sku", "content": {"*/*": {"schema": {"type": "object"}}}},
            "400": {"description": "Запрос не прошел валидацию", "content": {"*/*": {"schema": {"type": "object"}}}},
        },
        "request_base_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "spec_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/v3/api-docs",
        "swagger_ui_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/swagger-ui/index.html#/",
        "spec_hash": "sha256:spec",
        "operation_hash": "sha256:operation",
        "fetched_at": "2026-04-28T18:53:26.367820+00:00",
        "text": "POST /api/v1/sku/{skuId}/barcodes\nПривязка нового штрихкода",
    }


def test_compact_operation_result_omits_full_openapi_payload() -> None:
    compact = compact_operation_result(_operation(), score=0.8756)

    assert compact == {
        "score": 0.8756,
        "source_id": "fulfillment-catalog-pp-test",
        "service": "fulfillment-catalog",
        "env": "pp-test",
        "method": "POST",
        "path": "/api/v1/sku/{skuId}/barcodes",
        "operation_id": "addBarcodeToSku",
        "summary": "Привязка нового штрихкода к существующему товару",
        "tags": ["Сервис операций с sku (v1)"],
        "parameters": [
            {
                "name": "Partner-Id",
                "in": "header",
                "required": True,
                "description": "Идентификатор партнера",
                "schema": {"type": "string", "format": "uuid"},
            },
            {
                "name": "skuId",
                "in": "path",
                "required": True,
                "description": "Идентификатор sku",
                "schema": {"type": "string", "format": "uuid"},
            },
        ],
        "request_body": {
            "required": True,
            "content_types": ["application/json"],
            "json": {
                "required": ["type", "value"],
                "properties": {
                    "value": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 20,
                        "pattern": "[A-Z0-9\\-.$/+%() ]*",
                    },
                    "type": {"type": "string", "enum": ["PARTNER", "WMS"]},
                },
                "properties_count": 2,
            },
        },
        "response_codes": ["201", "400"],
        "success_response_codes": ["201"],
        "request_base_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "spec_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/v3/api-docs",
        "swagger_ui_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/swagger-ui/index.html#/",
        "spec_hash": "sha256:spec",
        "operation_hash": "sha256:operation",
        "fetched_at": "2026-04-28T18:53:26.367820+00:00",
        "next_tools": {
            "curl": {
                "tool": "rag_openapi_build_curl_template",
                "args": {
                    "service": "fulfillment-catalog",
                    "method": "POST",
                    "path": "/api/v1/sku/{skuId}/barcodes",
                },
            },
            "full_contract": {
                "tool": "rag_openapi_get_operation",
                "args": {
                    "service": "fulfillment-catalog",
                    "method": "POST",
                    "path": "/api/v1/sku/{skuId}/barcodes",
                },
            },
        },
    }
    assert "responses" not in compact
    assert "text" not in compact


def test_search_operations_returns_compact_results_by_default(monkeypatch) -> None:
    class FakeClient:
        def query_points(self, **kwargs):
            return SimpleNamespace(points=[SimpleNamespace(payload=_operation(), score=0.87564)])

    monkeypatch.setattr("qdrant_mcp.openapi_qdrant_store.ensure_collection_exists", lambda: None)
    monkeypatch.setattr("qdrant_mcp.openapi_qdrant_store.get_qdrant_client", lambda: FakeClient())

    results = search_operations(query_vector=[0.1, 0.2, 0.3])

    assert results[0]["score"] == 0.8756
    assert "responses" not in results[0]
    assert "request_body" in results[0]


def test_search_operations_does_not_accept_include_details() -> None:
    assert "include_details" not in inspect.signature(search_operations).parameters
