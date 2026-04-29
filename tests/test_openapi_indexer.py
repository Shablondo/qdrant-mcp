import json

from openapi_indexer import build_openapi_attachment


def test_build_openapi_attachment_includes_curl_template(monkeypatch) -> None:
    monkeypatch.setattr(
        "openapi_indexer.get_operation",
        lambda service, method, path: {
            "service": service,
            "env": "pp-test",
            "method": method.upper(),
            "path": path,
            "operation_id": "getSupply",
            "summary": "Get supply",
            "parameters": [{"name": "accept", "in": "header", "example": "application/json"}],
            "request_body": None,
            "responses": {"200": {"description": "OK"}},
            "spec_url": "https://example.test/v3/api-docs",
            "swagger_ui_url": "https://example.test/swagger-ui/index.html#/",
            "spec_hash": "sha256:spec",
            "operation_hash": "sha256:operation",
            "fetched_at": "2026-04-28T10:00:00Z",
            "request_base_url": "https://fulfillment-shipment-pp-test.k8s.5post-stage-5.salt.x5.ru",
        },
    )

    attachment = json.loads(build_openapi_attachment("fulfillment-shipment", "GET", "/api/v1/supplies/{{supplyId}}"))

    assert attachment["curlTemplate"].startswith("curl --location --globoff")
    assert "/api/v1/supplies/{{supplyId}}" in attachment["curlTemplate"]
    assert attachment["operationHash"] == "sha256:operation"
