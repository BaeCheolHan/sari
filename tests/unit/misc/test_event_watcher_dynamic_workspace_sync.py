"""watcher workspace 동적 동기화 동작을 검증한다."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from sari.services.collection.l1.event_watcher import EventWatcher


@dataclass(frozen=True)
class _WorkspaceStub:
    path: str
    is_active: bool = True


class _MutableWorkspaceRepoStub:
    def __init__(self, states: dict[str, bool]) -> None:
        self._states = states

    def list_all(self) -> list[_WorkspaceStub]:
        return [_WorkspaceStub(path=path, is_active=is_active) for path, is_active in self._states.items()]


class _FileRepoStub:
    def mark_deleted(self, repo_root: str, relative_path: str, now_iso: str) -> None:
        _ = (repo_root, relative_path, now_iso)


class _ObserverStub:
    def __init__(self) -> None:
        self._watch_counter = 0
        self.scheduled_paths: list[str] = []
        self.unscheduled_handles: list[object] = []

    def schedule(self, handler, path: str, recursive: bool):  # type: ignore[no-untyped-def]
        _ = handler
        assert recursive is True
        self._watch_counter += 1
        handle = ("watch", self._watch_counter, path)
        self.scheduled_paths.append(path)
        return handle

    def unschedule(self, watch: object) -> None:
        self.unscheduled_handles.append(watch)

    def start(self) -> None:
        return None


def test_watcher_sync_registers_newly_activated_workspace(tmp_path: Path) -> None:
    """inactive -> active 전환 시 watch 등록이 즉시 반영되어야 한다."""
    workspace = tmp_path / "repo-a"
    workspace.mkdir()
    states = {str(workspace.resolve()): False}
    repo = _MutableWorkspaceRepoStub(states)
    observer = _ObserverStub()

    watcher = EventWatcher(
        workspace_repo=repo,
        file_repo=_FileRepoStub(),
        candidate_index_sink=None,
        event_queue=queue.Queue(),
        stop_event=threading.Event(),
        debounce_events={},
        debounce_lock=threading.Lock(),
        watcher_debounce_ms=lambda: 10,
        assert_parent_alive=lambda worker_name: None,
        index_file_with_priority=lambda repo_root, relative_path, priority, enqueue_source: None,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        priority_high=90,
        set_observer=lambda obs: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
    )

    handler = object()
    watcher._sync_workspace_watches(observer=observer, handler=handler, force=True)  # noqa: SLF001
    assert observer.scheduled_paths == []

    states[str(workspace.resolve())] = True
    watcher._sync_workspace_watches(observer=observer, handler=handler, force=True)  # noqa: SLF001
    assert observer.scheduled_paths == [str(workspace.resolve())]


def test_watcher_sync_unschedules_deactivated_workspace_and_prunes_debounce(tmp_path: Path) -> None:
    """active -> inactive 전환 시 watch 해제 및 debounce 정리가 수행되어야 한다."""
    workspace = tmp_path / "repo-b"
    workspace.mkdir()
    repo_root = str(workspace.resolve())
    states = {repo_root: True}
    repo = _MutableWorkspaceRepoStub(states)
    observer = _ObserverStub()
    debounce_events: dict[tuple[str, str], tuple[float, str, str]] = {
        (repo_root, "a.py"): (0.0, "modified", ""),
        (repo_root, "b.py"): (0.0, "deleted", ""),
    }

    watcher = EventWatcher(
        workspace_repo=repo,
        file_repo=_FileRepoStub(),
        candidate_index_sink=None,
        event_queue=queue.Queue(),
        stop_event=threading.Event(),
        debounce_events=debounce_events,
        debounce_lock=threading.Lock(),
        watcher_debounce_ms=lambda: 10,
        assert_parent_alive=lambda worker_name: None,
        index_file_with_priority=lambda repo_root, relative_path, priority, enqueue_source: None,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        priority_high=90,
        set_observer=lambda obs: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
    )
    handler = object()
    watcher._sync_workspace_watches(observer=observer, handler=handler, force=True)  # noqa: SLF001
    assert len(observer.scheduled_paths) == 1

    states[repo_root] = False
    watcher._sync_workspace_watches(observer=observer, handler=handler, force=True)  # noqa: SLF001
    assert len(observer.unscheduled_handles) == 1
    assert debounce_events == {}
