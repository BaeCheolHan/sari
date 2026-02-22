"""watcher overflow 복구 예약 정책을 검증한다."""

from __future__ import annotations

import logging
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
    def __init__(self, roots: list[str], active_map: dict[str, bool] | None = None) -> None:
        self._roots = roots
        self._active_map = active_map or {}

    def list_all(self) -> list[_WorkspaceStub]:
        return [_WorkspaceStub(path=item, is_active=self._active_map.get(item, True)) for item in self._roots]


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


def test_event_watcher_loop_skips_inactive_workspace_paths(tmp_path: Path, monkeypatch, caplog) -> None:
    """watcher loop은 is_active=false workspace 경로를 스케줄하지 않아야 한다."""
    caplog.set_level(logging.DEBUG)
    workspace_active = tmp_path / "ws-active"
    workspace_inactive = tmp_path / "ws-inactive"
    workspace_active.mkdir()
    workspace_inactive.mkdir()
    scheduled_paths: list[str] = []

    class _ObserverStub:
        def schedule(self, handler, path: str, recursive: bool) -> None:  # type: ignore[no-untyped-def]
            _ = handler
            assert recursive is True
            scheduled_paths.append(path)

        def start(self) -> None:
            return None

    monkeypatch.setattr("sari.services.collection.event_watcher.Observer", _ObserverStub)

    stop_event = threading.Event()
    stop_event.set()
    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub(
            [str(workspace_active.resolve()), str(workspace_inactive.resolve())],
            active_map={str(workspace_inactive.resolve()): False},
        ),
        file_repo=_FileRepoStub(),
        candidate_index_sink=None,
        event_queue=queue.Queue(),
        stop_event=stop_event,
        debounce_events={},
        debounce_lock=threading.Lock(),
        watcher_debounce_ms=lambda: 10,
        assert_parent_alive=lambda worker_name: None,
        index_file_with_priority=lambda repo_root, relative_path, priority, enqueue_source: None,
        handle_background_collection_error=lambda exc, phase, worker_name: False,
        priority_high=90,
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
    )

    watcher.watcher_loop()

    assert str(workspace_active.resolve()) in scheduled_paths
    assert str(workspace_inactive.resolve()) not in scheduled_paths
    assert "inactive workspace skip" in caplog.text
    assert str(workspace_inactive.resolve()) in caplog.text


def test_event_watcher_selects_deepest_workspace_root_for_nested_paths(tmp_path: Path) -> None:
    """중첩 workspace에서 더 구체적인 경계를 repo_root로 선택해야 한다."""
    workspace_root = tmp_path / "workspace"
    nested_root = workspace_root / "nested"
    nested_root.mkdir(parents=True)
    nested_file = nested_root / "alpha.py"
    nested_file.write_text("print('nested')\n", encoding="utf-8")

    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub([str(workspace_root.resolve()), str(nested_root.resolve())]),
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
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
    )

    resolved_repo_root = watcher._resolve_repo_root_for_path(nested_file)  # noqa: SLF001
    assert resolved_repo_root == str(nested_root.resolve())


def test_event_watcher_ignores_inactive_workspace_during_event_matching(tmp_path: Path) -> None:
    """비활성 workspace 파일 이벤트는 디바운스 큐에 적재되지 않아야 한다."""
    workspace_inactive = tmp_path / "ws-inactive"
    workspace_inactive.mkdir()
    source_file = workspace_inactive / "beta.py"
    source_file.write_text("print('inactive')\n", encoding="utf-8")

    debounce_events: dict[tuple[str, str], tuple[float, str, str]] = {}
    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub(
            [str(workspace_inactive.resolve())],
            active_map={str(workspace_inactive.resolve()): False},
        ),
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
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
    )

    watcher.push_debounced_event("modified", str(source_file.resolve()), "")
    assert debounce_events == {}


def test_event_watcher_push_debounced_event_emits_cheap_signal_callback(tmp_path: Path) -> None:
    """push_debounced_event는 cheap signal callback에 repo_root/relative_path를 전달해야 한다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_file = workspace_root / "src" / "alpha.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("print('x')\n", encoding="utf-8")

    signals: list[tuple[str, str, str, str]] = []
    watcher = EventWatcher(
        workspace_repo=_WorkspaceRepoStub([str(workspace_root.resolve())]),
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
        set_observer=lambda observer: None,
        watcher_overflow_rescan_cooldown_sec=30,
        now_monotonic=lambda: 0.0,
        on_watcher_queue_overflow=lambda repo_root, src_path: None,
        schedule_rescan=lambda repo_root: None,
        on_watcher_signal=lambda et, rr, rel, dst: signals.append((et, rr, rel, dst)),
    )

    watcher.push_debounced_event("modified", str(source_file.resolve()), "")

    assert signals == [("modified", str(workspace_root.resolve()), "src/alpha.py", "")]
