from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import threading
from typing import Any, Iterable

from allure_sync import sync_allure_source
from confluence_sync import sync_confluence_source
from openapi_indexer import index_openapi_source
from rag_sources import RagSources, load_rag_sources
from sync_state_store import SyncState, get_sync_state, list_sync_states, save_sync_state


DEFAULT_RAG_SOURCES_PATH = Path(os.environ.get("RAG_SOURCES_PATH", "config/rag_sources.yaml"))
SOURCE_STATE_KIND = "rag_source"
DEFAULT_SYNC_MAX_WORKERS = 4
_SOURCE_LOCKS: dict[str, threading.Lock] = {}
_SOURCE_LOCKS_GUARD = threading.Lock()


def load_registry(path: str | Path | None = None) -> RagSources:
    return load_rag_sources(path or DEFAULT_RAG_SOURCES_PATH)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _max_workers_from_env(env_name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env_name, str(default))))
    except ValueError:
        return default


def _source_lock_key(source_kind: str, source: Any) -> str:
    return f"{source_kind}:{source.id}"


def _source_lock(source_kind: str, source: Any) -> threading.Lock:
    key = _source_lock_key(source_kind, source)
    with _SOURCE_LOCKS_GUARD:
        lock = _SOURCE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SOURCE_LOCKS[key] = lock
        return lock


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_dict(source: Any) -> dict[str, Any]:
    if is_dataclass(source):
        return asdict(source)
    if hasattr(source, "__dict__"):
        return dict(source.__dict__)
    return {
        key: getattr(source, key)
        for key in dir(source)
        if not key.startswith("_") and not callable(getattr(source, key))
    }


def _source_hash(source_kind: str, source: Any) -> str:
    encoded = json.dumps(
        {"source_kind": source_kind, "source": _source_dict(source)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _interval_minutes(source: Any, stale_after_minutes: int | None) -> int:
    source_interval = getattr(source, "sync_interval_minutes", None)
    if isinstance(source_interval, int) and source_interval > 0:
        return source_interval
    if isinstance(stale_after_minutes, int) and stale_after_minutes > 0:
        return stale_after_minutes
    return 0


def _next_due_at(last_synced_at: str | None, interval_minutes: int) -> str | None:
    last_synced = _parse_datetime(last_synced_at)
    if last_synced is None:
        return None
    return (last_synced + timedelta(minutes=interval_minutes)).isoformat()


def _is_due(previous: dict[str, Any] | None, interval_minutes: int, now_iso: str, *, force: bool) -> tuple[bool, str | None]:
    if force or interval_minutes <= 0 or previous is None:
        return True, None

    last_synced_at = str(previous.get("last_synced_at") or previous.get("synced_at") or "")
    next_due_at = str(previous.get("next_due_at") or _next_due_at(last_synced_at, interval_minutes) or "")
    next_due = _parse_datetime(next_due_at)
    now = _parse_datetime(now_iso)
    if next_due is None or now is None:
        return True, next_due_at or None
    return now >= next_due, next_due_at


def _save_source_status(
    *,
    source_kind: str,
    source: Any,
    status: str,
    now_iso: str,
    interval_minutes: int,
    previous: dict[str, Any] | None,
    result: dict[str, Any],
) -> None:
    previous_last_synced_at = str((previous or {}).get("last_synced_at") or (previous or {}).get("synced_at") or "")
    if status in {"success", "completed_with_errors"}:
        last_synced_at = now_iso
    else:
        last_synced_at = previous_last_synced_at or None

    next_due_at = _next_due_at(last_synced_at, interval_minutes) if last_synced_at else now_iso
    save_sync_state(
        SyncState(
            kind=SOURCE_STATE_KIND,
            source_id=str(source.id),
            content_hash=_source_hash(source_kind, source),
            version=source_kind,
            synced_at=now_iso,
            metadata={
                "source_kind": source_kind,
                "sync_interval_minutes": interval_minutes,
                "last_checked_at": now_iso,
                "last_synced_at": last_synced_at,
                "next_due_at": next_due_at,
                "last_status": status,
                "updated": int(result.get("updated", 0) or 0),
                "skipped": int(result.get("skipped", 0) or 0),
                "skipped_due": int(result.get("skipped_due", 0) or 0),
                "deleted": int(result.get("deleted", 0) or 0),
                "errors": int(result.get("errors", 0) or 0),
            },
        )
    )


def _sync_one_source(
    *,
    source_kind: str,
    source: Any,
    sync_fn: Any,
    stale_after_minutes: int | None,
    force: bool,
) -> dict[str, Any]:
    lock = _source_lock(source_kind, source)
    if not lock.acquire(blocking=False):
        return {
            "source_id": source.id,
            "source_kind": source_kind,
            "status": "skipped_locked",
            "due": False,
            "sync_interval_minutes": _interval_minutes(source, stale_after_minutes),
            "last_checked_at": _utc_now(),
            "updated": 0,
            "skipped": 0,
            "skipped_due": 0,
            "skipped_locked": 1,
            "deleted": 0,
            "errors": 0,
            "message": "Source sync is already running in this MCP process.",
        }

    try:
        return _sync_one_source_unlocked(
            source_kind=source_kind,
            source=source,
            sync_fn=sync_fn,
            stale_after_minutes=stale_after_minutes,
            force=force,
        )
    finally:
        lock.release()


def _sync_one_source_unlocked(
    *,
    source_kind: str,
    source: Any,
    sync_fn: Any,
    stale_after_minutes: int | None,
    force: bool,
) -> dict[str, Any]:
    now_iso = _utc_now()
    interval_minutes = _interval_minutes(source, stale_after_minutes)
    previous = get_sync_state(SOURCE_STATE_KIND, str(source.id))
    due, next_due_at = _is_due(previous, interval_minutes, now_iso, force=force)

    if not due:
        result = {
            "source_id": source.id,
            "source_kind": source_kind,
            "status": "skipped_not_due",
            "due": False,
            "sync_interval_minutes": interval_minutes,
            "last_checked_at": now_iso,
            "last_synced_at": (previous or {}).get("last_synced_at") or (previous or {}).get("synced_at"),
            "next_due_at": next_due_at,
            "updated": 0,
            "skipped": 0,
            "skipped_due": 1,
            "skipped_locked": 0,
            "deleted": 0,
            "errors": 0,
        }
        _save_source_status(
            source_kind=source_kind,
            source=source,
            status="skipped_not_due",
            now_iso=now_iso,
            interval_minutes=interval_minutes,
            previous=previous,
            result=result,
        )
        return result

    try:
        result = sync_fn(source)
        errors = int(result.get("errors", 0) or 0)
        status = "success" if errors == 0 else "completed_with_errors"
        result = {
            **result,
            "source_id": result.get("source_id", source.id),
            "source_kind": source_kind,
            "status": status,
            "due": True,
            "sync_interval_minutes": interval_minutes,
            "last_checked_at": now_iso,
            "last_synced_at": now_iso,
            "next_due_at": _next_due_at(now_iso, interval_minutes),
            "skipped_due": 0,
            "skipped_locked": 0,
        }
    except Exception as exc:
        result = {
            "source_id": source.id,
            "source_kind": source_kind,
            "status": "error",
            "due": True,
            "sync_interval_minutes": interval_minutes,
            "last_checked_at": now_iso,
            "last_synced_at": (previous or {}).get("last_synced_at") or (previous or {}).get("synced_at"),
            "next_due_at": now_iso,
            "updated": 0,
            "skipped": 0,
            "skipped_due": 0,
            "skipped_locked": 0,
            "deleted": 0,
            "errors": 1,
            "message": str(exc),
        }

    _save_source_status(
        source_kind=source_kind,
        source=source,
        status=str(result["status"]),
        now_iso=now_iso,
        interval_minutes=interval_minutes,
        previous=previous,
        result=result,
    )
    return result


def _run_source_tasks(
    tasks: list[tuple[str, Any, Any]],
    *,
    stale_after_minutes: int | None,
    force: bool,
) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {"confluence": [], "allure": [], "openapi": []}
    if not tasks:
        return results

    max_workers = min(_max_workers_from_env("RAG_SYNC_MAX_WORKERS", DEFAULT_SYNC_MAX_WORKERS), len(tasks))
    if max_workers <= 1:
        for source_kind, source, sync_fn in tasks:
            results[source_kind].append(
                _sync_one_source(
                    source_kind=source_kind,
                    source=source,
                    sync_fn=sync_fn,
                    stale_after_minutes=stale_after_minutes,
                    force=force,
                )
            )
        return results

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-sync-source") as executor:
        submitted = [
            (
                source_kind,
                executor.submit(
                    _sync_one_source,
                    source_kind=source_kind,
                    source=source,
                    sync_fn=sync_fn,
                    stale_after_minutes=stale_after_minutes,
                    force=force,
                ),
            )
            for source_kind, source, sync_fn in tasks
        ]
        for source_kind, future in submitted:
            results[source_kind].append(future.result())
    return results


def sync_sources(
    *,
    kinds: Iterable[str] | None = None,
    source_ids: Iterable[str] | None = None,
    stale_after_minutes: int | None = None,
    sources_path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    registry = load_registry(sources_path)
    requested_kinds = set(kinds or ("confluence", "allure", "openapi"))
    requested_ids = set(source_ids or [])
    tasks: list[tuple[str, Any, Any]] = []

    if "confluence" in requested_kinds:
        for source in registry.confluence:
            if requested_ids and source.id not in requested_ids:
                continue
            tasks.append(
                ("confluence", source, lambda item: sync_confluence_source(item, stale_after_minutes))
            )

    if "allure" in requested_kinds:
        for source in registry.allure:
            if requested_ids and source.id not in requested_ids:
                continue
            tasks.append(
                ("allure", source, lambda item: sync_allure_source(item, stale_after_minutes))
            )

    if "openapi" in requested_kinds:
        for source in registry.openapi:
            if requested_ids and source.id not in requested_ids:
                continue
            tasks.append(
                ("openapi", source, lambda item: index_openapi_source(item, reindex=False))
            )

    results = _run_source_tasks(tasks, stale_after_minutes=stale_after_minutes, force=force)

    return {
        "results": results,
        "totals": {
            kind: {
                "updated": sum(item.get("updated", 0) for item in items),
                "skipped": sum(item.get("skipped", 0) for item in items),
                "skipped_due": sum(item.get("skipped_due", 0) for item in items),
                "skipped_locked": sum(item.get("skipped_locked", 0) for item in items),
                "deleted": sum(item.get("deleted", 0) for item in items),
                "errors": sum(item.get("errors", 0) for item in items),
            }
            for kind, items in results.items()
        },
    }


def list_sources(sources_path: str | Path | None = None) -> dict[str, Any]:
    registry = load_registry(sources_path)
    return {
        "confluence": [source.__dict__ for source in registry.confluence],
        "allure": [source.__dict__ for source in registry.allure],
        "openapi": [source.__dict__ for source in registry.openapi],
    }


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def get_sync_status(
    kind: str | None = None,
    source_id_prefix: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    kind = _blank_to_none(kind)
    source_id_prefix = _blank_to_none(source_id_prefix)
    if not kind and not source_id_prefix:
        return {
            "states_count": 0,
            "returned_count": 0,
            "truncated": False,
            "requires_filter": True,
            "states": [],
            "usage": (
                "Pass kind and/or source_id_prefix to avoid dumping the whole sync-state collection. "
                "Examples: kind='rag_source', kind='openapi_operation', "
                "source_id_prefix='fulfillment-shipment-pp-test:'. "
                "For source freshness use rag_get_source_sync_status."
            ),
        }

    safe_limit = max(1, min(int(limit or 50), 100))
    states = list_sync_states(kind=kind, source_id_prefix=source_id_prefix, limit=safe_limit + 1)
    truncated = len(states) > safe_limit
    returned_states = states[:safe_limit]
    return {
        "states_count": len(returned_states),
        "returned_count": len(returned_states),
        "truncated": truncated,
        "limit": safe_limit,
        "kind": kind,
        "source_id_prefix": source_id_prefix,
        "states": returned_states,
    }


def _registry_sources(registry: RagSources) -> list[tuple[str, Any]]:
    return [
        *[("confluence", source) for source in registry.confluence],
        *[("allure", source) for source in registry.allure],
        *[("openapi", source) for source in registry.openapi],
    ]


def get_source_sync_status(sources_path: str | Path | None = None) -> dict[str, Any]:
    registry = load_registry(sources_path)
    states = {
        str(state.get("source_id")): state
        for state in list_sync_states(kind=SOURCE_STATE_KIND)
    }
    now_iso = _utc_now()
    sources = []
    for source_kind, source in _registry_sources(registry):
        interval_minutes = _interval_minutes(source, None)
        state = states.get(str(source.id), {})
        due, computed_next_due_at = _is_due(state, interval_minutes, now_iso, force=False)
        sources.append(
            {
                "id": source.id,
                "kind": source_kind,
                "sync_interval_minutes": interval_minutes,
                "last_checked_at": state.get("last_checked_at"),
                "last_synced_at": state.get("last_synced_at"),
                "next_due_at": state.get("next_due_at") or computed_next_due_at,
                "due": due,
                "last_status": state.get("last_status"),
                "updated": state.get("updated"),
                "skipped": state.get("skipped"),
                "skipped_due": state.get("skipped_due"),
                "deleted": state.get("deleted"),
                "errors": state.get("errors"),
            }
        )
    return {"sources_count": len(sources), "sources": sources}
