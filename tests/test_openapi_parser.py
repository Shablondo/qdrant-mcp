from qdrant_mcp.openapi_parser import normalize_openapi_operations


def test_normalizes_operations_with_stage5_runtime_url() -> None:
    spec = {
        "openapi": "3.0.1",
        "info": {"title": "Catalog API", "version": "1.0"},
        "paths": {
            "/api/v1/sku/import": {
                "post": {
                    "operationId": "importSku",
                    "summary": "Import SKU",
                    "tags": ["sku"],
                    "parameters": [
                        {"name": "Partner-Id", "in": "header", "schema": {"type": "string"}}
                    ],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "skuFile": {"type": "string", "format": "binary"}
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    operations = normalize_openapi_operations(
        spec,
        source_id="fulfillment-catalog-pp-test",
        service="fulfillment-catalog",
        env="pp-test",
        spec_url="https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/v3/api-docs",
        request_base_url="https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru",
        swagger_ui_url="https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/swagger-ui/index.html#/",
        spec_hash="sha256:abc",
        fetched_at="2026-04-28T10:00:00Z",
    )

    assert len(operations) == 1
    operation = operations[0]
    assert operation["operation_key"] == "fulfillment-catalog:POST:/api/v1/sku/import"
    assert operation["request_base_url"].endswith("5post-stage-5.salt.x5.ru")
    assert operation["method"] == "POST"
    assert operation["request_body"]["content"]["multipart/form-data"]
    assert operation["operation_hash"].startswith("sha256:")
