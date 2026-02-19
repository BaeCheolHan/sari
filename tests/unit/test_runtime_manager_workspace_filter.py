"""RuntimeManager의 workspace is_active 필터링 동작을 검증한다."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

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

    def _scan_once(path: str) -> None:
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
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        prune_error_events_if_needed=_prune_and_stop,
        watcher_loop=lambda: None,
    )

    manager._scheduler_loop()

    assert scanned_paths == ["/repo/active"]
    assert "inactive workspace skip" in caplog.text
    assert "/repo/inactive" in caplog.text
