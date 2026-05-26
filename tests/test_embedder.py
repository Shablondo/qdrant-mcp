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
