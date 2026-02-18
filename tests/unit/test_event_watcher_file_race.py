"""watcher 파일 경합(FILE_NOT_FOUND) 처리 정책을 검증한다."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from sari.services.collection.event_watcher import EventWatcher


@dataclass(frozen=True)
class _WorkspaceStub:
    path: str
    is_active: bool = True


class _WorkspaceRepoStub:
    def __init__(self, root: str) -> None:
        self._root = root

    def list_all(self) -> list[_WorkspaceStub]:
        return [_WorkspaceStub(path=self._root, is_active=True)]


class _FileRepoStub:
    def mark_deleted(self, repo_root: str, relative_path: str, now_iso: str) -> None:
        _ = (repo_root, relative_path, now_iso)


def test_event_watcher_file_not_found_is_treated_as_nonfatal_race(tmp_path: Path) -> None:
    """watcher 경합으로 파일이 사라진 경우 파이프라인 실패로 승격되면 안 된다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    missing_file = workspace_root / "gone.py"

    background_error_calls: list[tuple[str, str]] = []
    race_events: list[tuple[str, str, str]] = []

    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub(str(workspace_root)),
        file_repo=_FileRepoStub(),
        candidate_index_sink=None,
        event_queue=queue.Queue(),
        stop_event=threading.Event(),
        debounce_events={},
        debounce_lock=threading.Lock(),
        watcher_debounce_ms=lambda: 10,
        assert_parent_alive=lambda worker_name: None,
        index_file_with_priority=lambda repo_root, relative_path, priority, enqueue_source: (_ for _ in ()).throw(
            RuntimeError("should not be called for missing file")
        ),
        handle_background_collection_error=lambda exc, phase, worker_name: background_error_calls.append((phase, worker_name)) or False,
        priority_high=90,
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 100.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
        on_watcher_file_race=lambda repo_root, relative_path, reason: race_events.append((repo_root, relative_path, reason)),
    )

    watcher.handle_fs_event(event_type="modified", src_path=str(missing_file), dest_path="")

    assert background_error_calls == []
    assert len(race_events) == 1
    assert race_events[0][2] == "file_not_found"
