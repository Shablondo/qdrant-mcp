import threading
from qdrant_mcp.allure_sync import build_test_case_fingerprint
from qdrant_mcp.allure_sync import sync_allure_source
from types import SimpleNamespace


def test_build_test_case_fingerprint_changes_with_scenario() -> None:
    first = build_test_case_fingerprint(
        test_case={"id": 1, "updatedDate": "2026-04-28T10:00:00Z"},
        scenario={"steps": [{"name": "A"}]},
        attachments=[],
        tags=[],
    )
    second = build_test_case_fingerprint(
        test_case={"id": 1, "updatedDate": "2026-04-28T10:00:00Z"},
        scenario={"steps": [{"name": "B"}]},
        attachments=[],
        tags=[],
    )

    assert first != second
    assert first.startswith("sha256:")


def test_sync_allure_source_processes_test_cases_in_parallel(monkeypatch) -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    indexed_ids = []
    saved_states = []

    class FakeAllureClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def list_test_cases(self, project_id=None, rql=None):
            return [{"id": 1}, {"id": 2}]

        def get_complete_test_case(self, test_case_id):
            if test_case_id == 1:
                first_started.set()
                if not second_started.wait(0.5):
                    raise AssertionError("test case 2 did not start while test case 1 was running")
            if test_case_id == 2:
                second_started.set()
                if not first_started.wait(0.5):
                    raise AssertionError("test case 1 did not start while test case 2 was running")
            return {
                "test_case": {"id": test_case_id, "name": f"TC {test_case_id}", "updatedDate": "2026-04-29T10:00:00Z"},
                "scenario": {"steps": [{"name": "Step"}]},
                "attachments": [],
                "tags": [],
            }

    monkeypatch.setenv("RAG_ALLURE_SYNC_MAX_WORKERS", "2")
    monkeypatch.setattr("qdrant_mcp.allure_sync.AllureTestOpsClient", FakeAllureClient)
    monkeypatch.setattr("qdrant_mcp.allure_sync.get_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr(
        "qdrant_mcp.allure_sync.save_sync_states_batch",
        lambda states: saved_states.extend(states),
    )
    monkeypatch.setattr("qdrant_mcp.allure_sync.list_sync_states", lambda kind, source_id_prefix=None: [])
    monkeypatch.setattr("qdrant_mcp.allure_sync.delete_test_cases", lambda ids: None)
    monkeypatch.setattr("qdrant_mcp.allure_sync.delete_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr(
        "qdrant_mcp.allure_sync._build_chunks",
        lambda test_case_id, payload, project_id: {
            "chunks": [{"chunk_type": "scenario", "text": "step 1"}],
            "metadata": {"source": "allure", "project_id": str(project_id), "name": f"TC {test_case_id}"},
            "name": f"TC {test_case_id}",
        },
    )
    monkeypatch.setattr("qdrant_mcp.allure_sync.embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(
        "qdrant_mcp.allure_sync.upsert_test_cases_batch",
        lambda test_cases: [indexed_ids.append(int(tc["test_case_id"])) for tc in test_cases],
    )

    result = sync_allure_source(SimpleNamespace(id="allure-project-38", project_id=38))

    assert sorted(indexed_ids) == [1, 2]
    assert sorted(state.source_id for state in saved_states) == ["allure-project-38:1", "allure-project-38:2"]
    assert result["updated"] == 2
    assert result["errors"] == 0
