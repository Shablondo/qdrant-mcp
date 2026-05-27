import json
from types import SimpleNamespace

from qdrant_mcp.embedder import EmbedResponseError
from qdrant_mcp.openapi_indexer import build_openapi_attachment, index_openapi_source


def test_build_openapi_attachment_includes_curl_template(monkeypatch) -> None:
    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.get_operation",
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


def _make_operation(key_suffix: str) -> dict:
    return {
        "operation_key": f"svc:{key_suffix}",
        "operation_hash": f"hash:{key_suffix}",
        "service": "fulfillment-shipment",
        "method": "GET",
        "path": f"/api/v1/{key_suffix}",
        "env": "pp-test",
        "spec_url": "https://example.test/v3/api-docs",
        "spec_hash": "sha256:spec",
        "text": f"Operation {key_suffix}",
        "summary": f"Summary {key_suffix}",
    }


def test_index_openapi_source_flush_by_chunks(monkeypatch) -> None:
    monkeypatch.setenv("RAG_SYNC_FLUSH_CHUNKS", "2")

    operations = [_make_operation("a"), _make_operation("b"), _make_operation("c")]

    upsert_calls: list = []
    save_calls: list = []

    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.fetch_openapi_spec",
        lambda source: SimpleNamespace(spec={"openapi": "3.0"}, spec_hash="sha256:spec", fetched_at="2026-04-28T10:00:00Z"),
    )
    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.normalize_openapi_operations",
        lambda *args, **kwargs: operations,
    )
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.load_sync_states_dict", lambda kind, prefix: {})
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.upsert_operations_batch",
        lambda operations, content_vectors, title_vectors, operation_keys: upsert_calls.extend(operation_keys),
    )
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.save_sync_states_batch", lambda states: save_calls.extend(states))
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.list_sync_states", lambda kind, source_id_prefix: [])
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.delete_operations", lambda operation_keys: None)
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.delete_sync_state", lambda kind, source_id: None)

    result = index_openapi_source(
        SimpleNamespace(
            id="svc",
            service="fulfillment-shipment",
            env="pp-test",
            api_docs_url="https://example.test/v3/api-docs",
            request_base_url=None,
        )
    )

    assert result["updated"] == 3
    assert result["errors"] == 0
    assert len(upsert_calls) == 3


def test_index_openapi_source_embedder_error_on_batch(monkeypatch) -> None:
    monkeypatch.setenv("RAG_SYNC_FLUSH_CHUNKS", "1")

    operations = [_make_operation("a"), _make_operation("b"), _make_operation("c")]

    upsert_calls: list = []
    save_calls: list = []
    embed_call_count = [0]

    def failing_embed(texts):
        embed_call_count[0] += 1
        if embed_call_count[0] == 2:
            raise EmbedResponseError("embedder returned unexpected response: type=str preview='Error' batch_size=1")
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.fetch_openapi_spec",
        lambda source: SimpleNamespace(spec={"openapi": "3.0"}, spec_hash="sha256:spec", fetched_at="2026-04-28T10:00:00Z"),
    )
    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.normalize_openapi_operations",
        lambda *args, **kwargs: operations,
    )
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.load_sync_states_dict", lambda kind, prefix: {})
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.embed_texts", failing_embed)
    monkeypatch.setattr(
        "qdrant_mcp.openapi_indexer.upsert_operations_batch",
        lambda operations, content_vectors, title_vectors, operation_keys: upsert_calls.extend(operation_keys),
    )
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.save_sync_states_batch", lambda states: save_calls.extend(states))
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.list_sync_states", lambda kind, source_id_prefix: [])
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.delete_operations", lambda operation_keys: None)
    monkeypatch.setattr("qdrant_mcp.openapi_indexer.delete_sync_state", lambda kind, source_id: None)

    result = index_openapi_source(
        SimpleNamespace(
            id="svc",
            service="fulfillment-shipment",
            env="pp-test",
            api_docs_url="https://example.test/v3/api-docs",
            request_base_url=None,
        )
    )

    assert result["errors"] > 0
    assert len(upsert_calls) >= 1
    assert result["updated"] == len(upsert_calls)
