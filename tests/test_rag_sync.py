import threading

import rag_sync
from types import SimpleNamespace

from rag_sources import RagSources
from rag_sync import get_source_sync_status, get_sync_status, sync_sources


def test_sync_sources_dispatches_selected_kinds(monkeypatch) -> None:
    registry = RagSources(
        confluence=[SimpleNamespace(id="docs", root_page_id="1")],
        allure=[SimpleNamespace(id="allure", project_id=38)],
        openapi=[SimpleNamespace(id="catalog", service="catalog")],
    )
    calls = []

    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr(
        "rag_sync.sync_confluence_source",
        lambda source, stale_after_minutes=None: calls.append(("confluence", source.id)) or {"updated": 1},
    )
    monkeypatch.setattr(
        "rag_sync.sync_allure_source",
        lambda source, stale_after_minutes=None: calls.append(("allure", source.id)) or {"updated": 1},
    )
    monkeypatch.setattr(
        "rag_sync.index_openapi_source",
        lambda source, reindex=False: calls.append(("openapi", source.id)) or {"updated": 1},
    )
    monkeypatch.setattr("rag_sync.get_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: None)

    result = sync_sources(kinds=["confluence", "openapi"], force=True)

    assert calls == [("confluence", "docs"), ("openapi", "catalog")]
    assert result["totals"]["confluence"]["updated"] == 1
    assert result["totals"]["openapi"]["updated"] == 1
    assert result["totals"]["allure"]["updated"] == 0


def test_sync_sources_runs_independent_sources_in_parallel(monkeypatch) -> None:
    registry = RagSources(
        confluence=[SimpleNamespace(id="docs", root_page_id="1")],
        allure=[],
        openapi=[SimpleNamespace(id="catalog", service="catalog")],
    )
    confluence_started = threading.Event()
    openapi_started = threading.Event()

    def sync_confluence(source, stale_after_minutes=None):
        confluence_started.set()
        if not openapi_started.wait(0.5):
            raise AssertionError("openapi source did not start while confluence source was running")
        return {"updated": 1}

    def sync_openapi(source, reindex=False):
        openapi_started.set()
        if not confluence_started.wait(0.5):
            raise AssertionError("confluence source did not start while openapi source was running")
        return {"updated": 1}

    monkeypatch.setenv("RAG_SYNC_MAX_WORKERS", "2")
    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync.sync_confluence_source", sync_confluence)
    monkeypatch.setattr("rag_sync.index_openapi_source", sync_openapi)
    monkeypatch.setattr("rag_sync.get_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: None)

    result = sync_sources(kinds=["confluence", "openapi"], force=True)

    assert result["totals"]["confluence"]["updated"] == 1
    assert result["totals"]["openapi"]["updated"] == 1
    assert result["totals"]["confluence"]["errors"] == 0
    assert result["totals"]["openapi"]["errors"] == 0


def test_sync_sources_skips_locked_source_without_running_sync(monkeypatch) -> None:
    registry = RagSources(
        confluence=[SimpleNamespace(id="docs", root_page_id="1")],
        allure=[],
        openapi=[],
    )
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setitem(rag_sync._SOURCE_LOCKS, "confluence:docs", lock)
    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync.get_sync_state", lambda kind, source_id: None)
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: None)
    monkeypatch.setattr(
        "rag_sync.sync_confluence_source",
        lambda source, stale_after_minutes=None: (_ for _ in ()).throw(AssertionError("locked source should not sync")),
    )

    result = sync_sources(kinds=["confluence"], force=True)

    lock.release()
    assert result["results"]["confluence"][0]["status"] == "skipped_locked"
    assert result["totals"]["confluence"]["skipped_locked"] == 1


def test_sync_sources_skips_source_until_interval_expires(monkeypatch) -> None:
    registry = RagSources(
        confluence=[SimpleNamespace(id="docs", root_page_id="1", sync_interval_minutes=60)],
        allure=[],
        openapi=[],
    )
    calls = []
    saved_states = []

    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync._utc_now", lambda: "2026-04-28T11:00:00+00:00")
    monkeypatch.setattr(
        "rag_sync.get_sync_state",
        lambda kind, source_id: {
            "kind": "rag_source",
            "source_id": "docs",
            "last_synced_at": "2026-04-28T10:30:00+00:00",
            "next_due_at": "2026-04-28T11:30:00+00:00",
        },
    )
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: saved_states.append(state))
    monkeypatch.setattr(
        "rag_sync.sync_confluence_source",
        lambda source, stale_after_minutes=None: calls.append(source.id) or {"updated": 1},
    )

    result = sync_sources(kinds=["confluence"])

    assert calls == []
    assert result["totals"]["confluence"]["skipped_due"] == 1
    assert result["results"]["confluence"][0]["status"] == "skipped_not_due"
    assert saved_states[0].metadata["last_checked_at"] == "2026-04-28T11:00:00+00:00"
    assert saved_states[0].metadata["last_synced_at"] == "2026-04-28T10:30:00+00:00"
    assert saved_states[0].metadata["next_due_at"] == "2026-04-28T11:30:00+00:00"


def test_source_interval_takes_precedence_over_global_stale_after(monkeypatch) -> None:
    registry = RagSources(
        confluence=[],
        allure=[SimpleNamespace(id="allure", project_id=38, sync_interval_minutes=120)],
        openapi=[],
    )
    calls = []
    saved_states = []

    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync._utc_now", lambda: "2026-04-28T11:00:00+00:00")
    monkeypatch.setattr(
        "rag_sync.get_sync_state",
        lambda kind, source_id: {
            "last_synced_at": "2026-04-28T10:30:00+00:00",
            "next_due_at": "2026-04-28T12:30:00+00:00",
        },
    )
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: saved_states.append(state))
    monkeypatch.setattr(
        "rag_sync.sync_allure_source",
        lambda source, stale_after_minutes=None: calls.append(source.id) or {"updated": 1},
    )

    result = sync_sources(kinds=["allure"], stale_after_minutes=1440)

    assert calls == []
    assert result["results"]["allure"][0]["status"] == "skipped_not_due"
    assert result["results"]["allure"][0]["sync_interval_minutes"] == 120
    assert saved_states[0].metadata["next_due_at"] == "2026-04-28T12:30:00+00:00"


def test_sync_sources_runs_due_source_and_saves_source_status(monkeypatch) -> None:
    registry = RagSources(
        confluence=[],
        allure=[],
        openapi=[SimpleNamespace(id="catalog", service="catalog", sync_interval_minutes=30)],
    )
    calls = []
    saved_states = []

    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync._utc_now", lambda: "2026-04-28T11:00:00+00:00")
    monkeypatch.setattr(
        "rag_sync.get_sync_state",
        lambda kind, source_id: {
            "last_synced_at": "2026-04-28T10:20:00+00:00",
            "next_due_at": "2026-04-28T10:50:00+00:00",
        },
    )
    monkeypatch.setattr("rag_sync.save_sync_state", lambda state: saved_states.append(state))
    monkeypatch.setattr(
        "rag_sync.index_openapi_source",
        lambda source, reindex=False: calls.append(source.id) or {"updated": 2, "skipped": 3, "deleted": 1, "errors": 0},
    )

    result = sync_sources(kinds=["openapi"])

    assert calls == ["catalog"]
    assert result["totals"]["openapi"]["updated"] == 2
    assert result["results"]["openapi"][0]["status"] == "success"
    assert saved_states[0].kind == "rag_source"
    assert saved_states[0].source_id == "catalog"
    assert saved_states[0].metadata["last_checked_at"] == "2026-04-28T11:00:00+00:00"
    assert saved_states[0].metadata["last_synced_at"] == "2026-04-28T11:00:00+00:00"
    assert saved_states[0].metadata["next_due_at"] == "2026-04-28T11:30:00+00:00"
    assert saved_states[0].metadata["updated"] == 2


def test_get_source_sync_status_reports_due_state(monkeypatch) -> None:
    registry = RagSources(
        confluence=[],
        allure=[SimpleNamespace(id="allure", project_id=38, sync_interval_minutes=120)],
        openapi=[],
    )

    monkeypatch.setattr("rag_sync.load_registry", lambda sources_path=None: registry)
    monkeypatch.setattr("rag_sync._utc_now", lambda: "2026-04-28T13:00:00+00:00")
    monkeypatch.setattr(
        "rag_sync.list_sync_states",
        lambda kind=None, source_id_prefix=None: [
            {
                "kind": "rag_source",
                "source_id": "allure",
                "source_kind": "allure",
                "last_checked_at": "2026-04-28T10:00:00+00:00",
                "last_synced_at": "2026-04-28T10:00:00+00:00",
                "next_due_at": "2026-04-28T12:00:00+00:00",
                "last_status": "success",
            }
        ],
    )

    status = get_source_sync_status()

    assert status["sources_count"] == 1
    assert status["sources"][0]["id"] == "allure"
    assert status["sources"][0]["due"] is True
    assert status["sources"][0]["next_due_at"] == "2026-04-28T12:00:00+00:00"


def test_get_sync_status_requires_filter_for_blank_values(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("list_sync_states should not be called without filters")

    monkeypatch.setattr("rag_sync.list_sync_states", fail_if_called)

    status = get_sync_status(kind="", source_id_prefix="")

    assert status["states"] == []
    assert status["states_count"] == 0
    assert status["requires_filter"] is True
    assert "kind" in status["usage"]


def test_get_sync_status_limits_filtered_results(monkeypatch) -> None:
    requested = {}

    def fake_list_sync_states(kind=None, source_id_prefix=None, limit=None):
        requested.update({"kind": kind, "source_id_prefix": source_id_prefix, "limit": limit})
        return [
            {"kind": "openapi_operation", "source_id": f"shipment:{index}"}
            for index in range(3)
        ]

    monkeypatch.setattr("rag_sync.list_sync_states", fake_list_sync_states)

    status = get_sync_status(kind=" openapi_operation ", source_id_prefix=" shipment: ", limit=2)

    assert requested == {"kind": "openapi_operation", "source_id_prefix": "shipment:", "limit": 3}
    assert status["states_count"] == 2
    assert status["returned_count"] == 2
    assert status["truncated"] is True
    assert [state["source_id"] for state in status["states"]] == ["shipment:0", "shipment:1"]
