"""watcher overflow 복구 예약 정책을 검증한다."""

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
    def __init__(self, roots: list[str]) -> None:
        self._roots = roots

    def list_all(self) -> list[_WorkspaceStub]:
        return [_WorkspaceStub(path=item, is_active=True) for item in self._roots]


class _FileRepoStub:
    def mark_deleted(self, repo_root: str, relative_path: str, now_iso: str) -> None:
        _ = (repo_root, relative_path, now_iso)


class _NowMonotonicStub:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_event_watcher_queue_overflow_schedules_rescan_with_cooldown(tmp_path: Path) -> None:
    """큐 오버플로우가 연속 발생해도 cooldown 동안 재스캔 예약은 1회여야 한다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_file = workspace_root / "alpha.py"
    source_file.write_text("print('x')\n", encoding="utf-8")

    event_queue: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=1)
    # 큐를 먼저 채워 overflow를 강제로 만든다.
    event_queue.put(("created", str(source_file), ""))

    overflow_events: list[tuple[str | None, str]] = []
    scheduled_roots: list[str] = []
    clock = _NowMonotonicStub(100.0)

    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub([str(workspace_root)]),
        file_repo=_FileRepoStub(),
        candidate_index_sink=None,
        event_queue=event_queue,
        stop_event=threading.Event(),
        debounce_events={},
        debounce_lock=threading.Lock(),
        watcher_debounce_ms=lambda: 10,
        assert_parent_alive=lambda worker_name: None,
        index_file_with_priority=lambda repo_root, relative_path, priority, enqueue_source: None,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        priority_high=90,
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=clock,
        on_watcher_queue_overflow=lambda repo_root, src_path: overflow_events.append((repo_root, src_path)),
        schedule_rescan=lambda repo_root: scheduled_roots.append(repo_root),
    )

    watcher.enqueue_event("modified", str(source_file), "")
    watcher.enqueue_event("modified", str(source_file), "")
    watcher.process_pending_rescans()

    assert len(overflow_events) == 2
    assert len(scheduled_roots) == 1
    assert scheduled_roots[0] == str(workspace_root.resolve())
