from qdrant_mcp.openapi_curl import build_curl_template


def test_builds_json_curl_template() -> None:
    operation = {
        "request_base_url": "https://fulfillment-shipment-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "method": "POST",
        "path": "/api/v1/supplies",
        "parameters": [
            {"name": "accept", "in": "header", "schema": {"type": "string"}, "example": "application/json"},
            {"name": "Partner-Id", "in": "header", "schema": {"type": "string"}},
            {"name": "User-Id", "in": "header", "schema": {"type": "string"}},
        ],
        "request_body": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "plannedShipmentAt": {"type": "string", "format": "date"},
                            "quantity": {"type": "integer"},
                        },
                    }
                }
            }
        },
    }

    curl = build_curl_template(operation)

    assert "curl --location 'https://fulfillment-shipment-pp-test.k8s.5post-stage-5.salt.x5.ru/api/v1/supplies'" in curl
    assert "--header 'Partner-Id: {{Partner-Id}}'" in curl
    assert "--header 'Content-Type: application/json'" in curl
    assert '"plannedShipmentAt": "{{plannedShipmentAt}}"' in curl
    assert '"quantity": 1' in curl


def test_adds_globoff_for_parameterized_url() -> None:
    operation = {
        "request_base_url": "https://fulfillment-shipment-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "method": "GET",
        "path": "/api/v1/supplies/{{supplyId}}",
        "parameters": [{"name": "accept", "in": "header", "example": "application/json"}],
        "request_body": None,
    }

    curl = build_curl_template(operation)

    assert "curl --location --globoff" in curl
    assert "/api/v1/supplies/{{supplyId}}" in curl


def test_converts_openapi_path_parameters_to_curl_placeholders() -> None:
    operation = {
        "request_base_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "method": "POST",
        "path": "/api/v1/sku/{skuId}/barcodes",
        "parameters": [
            {"name": "accept", "in": "header", "example": "application/json"},
            {"name": "Partner-Id", "in": "header", "schema": {"type": "string"}},
            {"name": "User-Id", "in": "header", "schema": {"type": "string"}},
        ],
        "request_body": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "type": {"type": "string", "enum": ["PARTNER", "WMS"]},
                        },
                        "required": ["type", "value"],
                    }
                }
            },
            "required": True,
        },
    }

    curl = build_curl_template(operation)

    assert "curl --location --globoff" in curl
    assert "/api/v1/sku/{{skuId}}/barcodes" in curl
    assert '"value": "{{value}}"' in curl
    assert '"type": "{{type}}"' in curl


def test_builds_multipart_file_curl_template() -> None:
    operation = {
        "request_base_url": "https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru",
        "method": "POST",
        "path": "/api/v1/sku/import",
        "parameters": [
            {"name": "accept", "in": "header", "example": "application/json"},
            {"name": "Partner-Id", "in": "header", "schema": {"type": "string"}},
            {"name": "User-Id", "in": "header", "schema": {"type": "string"}},
        ],
        "request_body": {
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
    }

    curl = build_curl_template(operation)

    assert "curl --location 'https://fulfillment-catalog-pp-test.k8s.5post-stage-5.salt.x5.ru/api/v1/sku/import'" in curl
    assert "--header 'Partner-Id: {{Partner-Id}}'" in curl
    assert "--form 'skuFile=@\"{{skuFilePath}}\"'" in curl
    assert "--header 'Content-Type: multipart/form-data'" not in curl
