from unittest.mock import MagicMock

from qdrant_mcp.embedder import EmbedResponseError, embed_texts


def test_embed_texts_raises_embed_response_error_on_unexpected_response(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = "Request Rejected"

    monkeypatch.setattr("qdrant_mcp.embedder._get_client", lambda: mock_client)

    try:
        embed_texts(["x"])
    except EmbedResponseError as exc:
        msg = str(exc)
        assert "type=str" in msg
        assert "Request Rejected" in msg
        return

    raise AssertionError("Expected EmbedResponseError was not raised")


def test_embed_texts_wraps_sdk_exception_as_embed_response_error(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = RuntimeError("503 Service Unavailable")

    monkeypatch.setattr("qdrant_mcp.embedder._get_client", lambda: mock_client)

    try:
        embed_texts(["x"])
    except EmbedResponseError as exc:
        msg = str(exc)
        assert "embedder API call failed" in msg
        assert "type=RuntimeError" in msg
        assert "503 Service Unavailable" in msg
        assert "batch_size=1" in msg
        return

    raise AssertionError("Expected EmbedResponseError was not raised")
