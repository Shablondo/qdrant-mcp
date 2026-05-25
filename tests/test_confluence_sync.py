from types import SimpleNamespace

from qdrant_mcp.confluence_sync import content_hash, page_changed, sync_confluence_source


def test_page_changed_when_version_differs() -> None:
    current = {"version": "3", "content_hash": "sha256:new"}
    previous = {"version": "2", "content_hash": "sha256:old"}

    assert page_changed(current, previous) is True


def test_page_unchanged_when_version_and_hash_match() -> None:
    current = {"version": "3", "content_hash": "sha256:same"}
    previous = {"version": "3", "content_hash": "sha256:same"}

    assert page_changed(current, previous) is False


def test_content_hash_is_stable() -> None:
    assert content_hash("same text") == content_hash("same text")
    assert content_hash("same text") != content_hash("different text")


def test_sync_confluence_source_walks_root_and_nested_children(monkeypatch) -> None:
    pages = {
        "1": {
            "id": "1",
            "title": "Root",
            "body_html": "<p>Root page</p>",
            "url": "https://confluence/pages/1",
            "space_key": "FUL",
            "last_modified": "2026-04-28T10:00:00Z",
            "version": "1",
        },
        "2": {
            "id": "2",
            "title": "Child",
            "body_html": "<p>Child page</p>",
            "url": "https://confluence/pages/2",
            "space_key": "FUL",
            "last_modified": "2026-04-28T10:01:00Z",
            "version": "1",
        },
        "3": {
            "id": "3",
            "title": "Nested",
            "body_html": "<p>Nested page</p>",
            "url": "https://confluence/pages/3",
            "space_key": "FUL",
            "last_modified": "2026-04-28T10:02:00Z",
            "version": "1",
        },
    }
    children = {"1": ["2"], "2": ["3"], "3": []}
    indexed = []
    saved_states = []

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("qdrant_mcp.confluence_sync._get_http_client", lambda: DummyClient())
    monkeypatch.setattr("qdrant_mcp.confluence_sync._fetch_page", lambda client, page_id: pages[page_id])
    monkeypatch.setattr("qdrant_mcp.confluence_sync._fetch_child_pages", lambda client, page_id: children[page_id])
    monkeypatch.setattr("qdrant_mcp.confluence_sync._html_to_text", lambda html: html)
    monkeypatch.setattr("qdrant_mcp.confluence_sync._chunk_text", lambda text, title: [f"{title}: {text}"])
    monkeypatch.setattr("qdrant_mcp.confluence_sync.embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr("qdrant_mcp.confluence_sync._build_title_vectors", lambda title, count: [[0.3, 0.4] for _ in range(count)])
    monkeypatch.setattr("qdrant_mcp.confluence_sync.get_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr("qdrant_mcp.confluence_sync.save_sync_state", lambda state: saved_states.append(state))
    monkeypatch.setattr("qdrant_mcp.confluence_sync.delete_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr("qdrant_mcp.confluence_sync.list_sync_states", lambda kind, source_id_prefix: [])
    monkeypatch.setattr("qdrant_mcp.confluence_sync.delete_page", lambda page_id: None)
    monkeypatch.setattr(
        "qdrant_mcp.confluence_sync.upsert_page_chunks",
        lambda **kwargs: indexed.append(kwargs) or len(kwargs["chunks"]),
    )

    result = sync_confluence_source(SimpleNamespace(id="docs", root_page_id="1"))

    assert result["updated"] == 3
    assert [item["page_id"] for item in indexed] == ["1", "2", "3"]
    assert all(item["metadata"]["root_page_id"] == "1" for item in indexed)
    assert {state.source_id for state in saved_states} == {"docs:1", "docs:2", "docs:3"}
