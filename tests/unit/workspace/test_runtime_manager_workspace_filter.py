"""RuntimeManager의 workspace is_active 필터링 동작을 검증한다."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass

from sari.core.exceptions import ErrorContext, ValidationError
from sari.services.collection.runtime_manager import RuntimeManager


@dataclass(frozen=True)
class _WorkspaceStub:
    path: str
    is_active: bool


class _WorkspaceRepoStub:
    def __init__(self, items: list[_WorkspaceStub]) -> None:
        self._items = items

    def list_all(self) -> list[_WorkspaceStub]:
        return list(self._items)


class _EnrichQueueRepoStub:
    def recover_stale_running_to_failed(self, now_iso: str, stale_before_iso: str) -> int:
        _ = (now_iso, stale_before_iso)
        return 0

    def reset_running_to_failed(self, now_iso: str) -> None:
        _ = now_iso


@dataclass(frozen=True)
class _PolicyStub:
    scan_interval_sec: int = 0
    max_enrich_batch: int = 1
    queue_poll_interval_ms: int = 1


def test_runtime_manager_scheduler_skips_inactive_workspaces(caplog) -> None:
    """scheduler는 is_active=false workspace를 스캔하지 않아야 한다."""
    caplog.set_level(logging.DEBUG)
    stop_event = threading.Event()
    scanned_paths: list[str] = []
    workspaces = [
        _WorkspaceStub(path="/repo/active", is_active=True),
        _WorkspaceStub(path="/repo/inactive", is_active=False),
    ]

    def _scan_once(path: str, *, trigger: str = "manual") -> None:
        assert trigger == "background"
        scanned_paths.append(path)

    def _prune_and_stop() -> None:
        stop_event.set()

    manager = RuntimeManager(
        stop_event=stop_event,
        enrich_queue_repo=_EnrichQueueRepoStub(),
        workspace_repo=_WorkspaceRepoStub(workspaces),
        policy=_PolicyStub(),
        policy_repo=None,
        assert_parent_alive=lambda worker_name: None,
        scan_once=_scan_once,
        process_enrich_jobs_bootstrap=lambda batch: 0,
        process_enrich_jobs_l5=lambda batch: 0,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        prune_error_events_if_needed=_prune_and_stop,
        watcher_loop=lambda: None,
    )

    manager._scheduler_loop()

    assert scanned_paths == ["/repo/active"]
    assert "inactive workspace skip" in caplog.text
    assert "/repo/inactive" in caplog.text


def test_runtime_manager_enrich_l5_loop_runs_processor_once() -> None:
    """L5 루프는 전용 processor를 호출하고 stop 시 종료해야 한다."""
    stop_event = threading.Event()
    calls: list[int] = []

    def _process_l5(batch: int) -> int:
        calls.append(batch)
        stop_event.set()
        return 1

    manager = RuntimeManager(
        stop_event=stop_event,
        enrich_queue_repo=_EnrichQueueRepoStub(),
        workspace_repo=_WorkspaceRepoStub([]),
        policy=_PolicyStub(max_enrich_batch=7),
        policy_repo=None,
        assert_parent_alive=lambda worker_name: None,
        scan_once=lambda path: None,
        process_enrich_jobs_bootstrap=lambda batch: 0,
        process_enrich_jobs_l5=_process_l5,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        prune_error_events_if_needed=lambda: None,
        watcher_loop=lambda: None,
    )

    manager._enrich_l5_loop()

    assert calls == [7]


def test_runtime_manager_enrich_l5_loop_handles_validation_error() -> None:
    """L5 루프는 ValidationError를 CollectionError로 승격해 핸들러로 전달해야 한다."""
    stop_event = threading.Event()
    handled: list[tuple[str, str, str]] = []

    def _process_l5(batch: int) -> int:
        _ = batch
        raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="parent_symbol_key must be non-empty str"))

    def _handle(exc, phase: str, worker_name: str):  # noqa: ANN001
        handled.append((exc.context.code, phase, worker_name))
        stop_event.set()
        return False

    manager = RuntimeManager(
        stop_event=stop_event,
        enrich_queue_repo=_EnrichQueueRepoStub(),
        workspace_repo=_WorkspaceRepoStub([]),
        policy=_PolicyStub(max_enrich_batch=3),
        policy_repo=None,
        assert_parent_alive=lambda worker_name: None,
        scan_once=lambda path: None,
        process_enrich_jobs_bootstrap=lambda batch: 0,
        process_enrich_jobs_l5=_process_l5,
        handle_background_collection_error=_handle,
        prune_error_events_if_needed=lambda: None,
        watcher_loop=lambda: None,
    )

    manager._enrich_l5_loop()
    assert handled == [("ERR_L5_SYMBOL_MAPPING", "enrich_l5_loop_validation", "enrich_worker_l5")]


def test_runtime_manager_enrich_bootstrap_loop_uses_non_l5_validation_code() -> None:
    """bootstrap 루프 ValidationError는 L5 전용 코드로 라벨링되면 안 된다."""
    stop_event = threading.Event()
    handled: list[tuple[str, str, str]] = []

    def _process_bootstrap(batch: int) -> int:
        _ = batch
        raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message="bootstrap mapping invalid"))

    def _handle(exc, phase: str, worker_name: str):  # noqa: ANN001
        handled.append((exc.context.code, phase, worker_name))
        stop_event.set()
        return False

    manager = RuntimeManager(
        stop_event=stop_event,
        enrich_queue_repo=_EnrichQueueRepoStub(),
        workspace_repo=_WorkspaceRepoStub([]),
        policy=_PolicyStub(max_enrich_batch=3),
        policy_repo=None,
        assert_parent_alive=lambda worker_name: None,
        scan_once=lambda path: None,
        process_enrich_jobs_bootstrap=_process_bootstrap,
        process_enrich_jobs_l5=lambda batch: 0,
        handle_background_collection_error=_handle,
        prune_error_events_if_needed=lambda: None,
        watcher_loop=lambda: None,
    )

    manager._enrich_loop()
    assert handled == [("ERR_COLLECTION_VALIDATION", "enrich_loop_validation", "enrich_worker")]


def test_runtime_manager_scheduler_handles_sqlite_scan_error_without_thread_exit() -> None:
    """scheduler scan 중 sqlite 오류가 발생해도 핸들러로 전달 후 다음 workspace를 계속 처리해야 한다."""
    stop_event = threading.Event()
    scanned_paths: list[str] = []
    handled: list[tuple[str, str, str, str]] = []
    workspaces = [
        _WorkspaceStub(path="/repo/locked", is_active=True),
        _WorkspaceStub(path="/repo/ok", is_active=True),
    ]

    def _scan_once(path: str, *, trigger: str = "manual") -> None:
        assert trigger == "background"
        if path == "/repo/locked":
            raise sqlite3.OperationalError("database is locked")
        scanned_paths.append(path)

    def _handle(exc, phase: str, worker_name: str):  # noqa: ANN001
        handled.append((exc.context.code, phase, worker_name, exc.context.message))
        return False

    def _prune_and_stop() -> None:
        stop_event.set()

    manager = RuntimeManager(
        stop_event=stop_event,
        enrich_queue_repo=_EnrichQueueRepoStub(),
        workspace_repo=_WorkspaceRepoStub(workspaces),
        policy=_PolicyStub(),
        policy_repo=None,
        assert_parent_alive=lambda worker_name: None,
        scan_once=_scan_once,
        process_enrich_jobs_bootstrap=lambda batch: 0,
        process_enrich_jobs_l5=lambda batch: 0,
        handle_background_collection_error=_handle,
        prune_error_events_if_needed=_prune_and_stop,
        watcher_loop=lambda: None,
    )

    manager._scheduler_loop()

    assert scanned_paths == ["/repo/ok"]
    assert len(handled) == 1
    assert handled[0][0] == "ERR_COLLECTION_DB_FATAL"
    assert handled[0][1] == "scheduler_scan_db"
    assert handled[0][2] == "scheduler"
    assert "database is locked" in handled[0][3]
