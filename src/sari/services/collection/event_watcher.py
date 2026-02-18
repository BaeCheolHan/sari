"""파일 시스템 이벤트 감시 전용 컴포넌트."""

from __future__ import annotations

import queue
import time
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from sari.core.exceptions import CollectionError
from sari.core.models import now_iso8601_utc


class _WatcherHandler(FileSystemEventHandler):
    """watchdog 이벤트를 내부 큐로 전달한다."""

    def __init__(self, enqueue_event: Callable[[str, str, str], None]) -> None:
        """이벤트 enqueue 함수를 저장한다."""
        super().__init__()
        self._enqueue_event_fn = enqueue_event

    def on_created(self, event: FileSystemEvent) -> None:
        """생성 이벤트를 큐에 적재한다."""
        self._enqueue_event("created", event)

    def on_modified(self, event: FileSystemEvent) -> None:
        """수정 이벤트를 큐에 적재한다."""
        self._enqueue_event("modified", event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """삭제 이벤트를 큐에 적재한다."""
        self._enqueue_event("deleted", event)

    def on_moved(self, event: FileSystemEvent) -> None:
        """이동 이벤트를 큐에 적재한다."""
        self._enqueue_event("moved", event)

    def _enqueue_event(self, event_type: str, event: FileSystemEvent) -> None:
        """디렉터리 이벤트를 제외하고 파일 이벤트를 큐에 적재한다."""
        if event.is_directory:
            return
        self._enqueue_event_fn(event_type, str(event.src_path), str(getattr(event, "dest_path", "")))


class EventWatcher:
    """watcher 루프/디바운스 처리 책임을 담당한다."""

    def __init__(
        self,
        *,
        workspace_repo: object,
        file_repo: object,
        candidate_index_sink: object | None,
        event_queue: queue.Queue[tuple[str, str, str]],
        stop_event: Event,
        debounce_events: dict[tuple[str, str], tuple[float, str, str]],
        debounce_lock: Lock,
        watcher_debounce_ms: Callable[[], int],
        assert_parent_alive: Callable[[str], None],
        index_file_with_priority: Callable[[str, str, int, str], None],
        handle_background_collection_error: Callable[[CollectionError, str, str], bool],
        priority_high: int,
        set_observer: Callable[[Observer | None], None],
        watcher_overflow_rescan_cooldown_sec: int,
        now_monotonic: Callable[[], float],
        on_watcher_queue_overflow: Callable[[str | None, str], None],
        schedule_rescan: Callable[[str], None],
        on_watcher_file_race: Callable[[str, str, str], None] | None = None,
    ) -> None:
        """watcher 동작에 필요한 의존성만 주입받는다."""
        self._workspace_repo = workspace_repo
        self._file_repo = file_repo
        self._candidate_index_sink = candidate_index_sink
        self._event_queue = event_queue
        self._stop_event = stop_event
        self._debounce_events = debounce_events
        self._debounce_lock = debounce_lock
        self._watcher_debounce_ms = watcher_debounce_ms
        self._assert_parent_alive = assert_parent_alive
        self._index_file_with_priority = index_file_with_priority
        self._handle_background_collection_error = handle_background_collection_error
        self._priority_high = priority_high
        self._set_observer = set_observer
        self._watcher_overflow_rescan_cooldown_sec = max(1, int(watcher_overflow_rescan_cooldown_sec))
        self._now_monotonic = now_monotonic
        self._on_watcher_queue_overflow = on_watcher_queue_overflow
        self._schedule_rescan = schedule_rescan
        self._on_watcher_file_race = on_watcher_file_race
        self._pending_rescan_roots: set[str] = set()
        self._overflow_last_by_repo: dict[str, float] = {}
        self._overflow_lock = Lock()

    def watcher_loop(self) -> None:
        """watchdog 이벤트 루프를 실행한다."""
        observer = Observer()
        handler = _WatcherHandler(self.enqueue_event)
        workspaces = self._workspace_repo.list_all()
        for workspace in workspaces:
            if not workspace.is_active:
                continue
            observer.schedule(handler, workspace.path, recursive=True)
        observer.start()
        self._set_observer(observer)
        while not self._stop_event.is_set():
            self._assert_parent_alive(worker_name="watcher")
            self.flush_debounced_events()
            self.process_pending_rescans()
            try:
                event_type, src_path, dest_path = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self.push_debounced_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def enqueue_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """watchdog 이벤트를 큐에 적재하고 overflow를 감지한다."""
        try:
            self._event_queue.put_nowait((event_type, src_path, dest_path))
            return
        except queue.Full:
            repo_root = self._resolve_repo_root_for_path(Path(src_path))
            self._on_watcher_queue_overflow(repo_root, src_path)
            if repo_root is None:
                return
            now_monotonic = self._now_monotonic()
            with self._overflow_lock:
                last_seen = self._overflow_last_by_repo.get(repo_root)
                if (
                    last_seen is None
                    or (now_monotonic - last_seen) >= float(self._watcher_overflow_rescan_cooldown_sec)
                ):
                    self._overflow_last_by_repo[repo_root] = now_monotonic
                    self._pending_rescan_roots.add(repo_root)

    def process_pending_rescans(self) -> None:
        """overflow로 누락된 이벤트를 보정하기 위한 repo 재스캔을 수행한다."""
        with self._overflow_lock:
            pending_roots = sorted(self._pending_rescan_roots)
            self._pending_rescan_roots.clear()
        for repo_root in pending_roots:
            try:
                self._schedule_rescan(repo_root)
            except CollectionError as exc:
                if self._handle_background_collection_error(exc=exc, phase="watcher_overflow_rescan", worker_name="watcher"):
                    raise
                return

    def handle_fs_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """단일 파일 시스템 이벤트를 처리한다."""
        source_path = Path(src_path).resolve()
        workspaces = self._workspace_repo.list_all()
        matched_root: Path | None = None
        for workspace in workspaces:
            root = Path(workspace.path).resolve()
            if _path_is_relative_to(source_path, root):
                matched_root = root
                break
        if matched_root is None:
            return
        relative_path = str(source_path.relative_to(matched_root).as_posix())
        if event_type != "deleted" and not source_path.exists():
            self._record_file_race(repo_root=str(matched_root), relative_path=relative_path, reason="file_not_found")
            return
        if event_type == "deleted":
            self._file_repo.mark_deleted(str(matched_root), relative_path, now_iso8601_utc())
            if self._candidate_index_sink is not None:
                self._candidate_index_sink.record_delete(str(matched_root), relative_path, "watcher_deleted")
            return
        if event_type == "moved" and dest_path.strip() != "":
            self._file_repo.mark_deleted(str(matched_root), relative_path, now_iso8601_utc())
            if self._candidate_index_sink is not None:
                self._candidate_index_sink.record_delete(str(matched_root), relative_path, "watcher_moved")
            moved_dest = Path(dest_path).resolve()
            if _path_is_relative_to(moved_dest, matched_root):
                dest_relative = str(moved_dest.relative_to(matched_root).as_posix())
                if not moved_dest.exists():
                    self._record_file_race(repo_root=str(matched_root), relative_path=dest_relative, reason="file_not_found")
                    return
                try:
                    self._index_file_with_priority(str(matched_root), dest_relative, self._priority_high, "watcher")
                except CollectionError as exc:
                    if exc.context.code == "ERR_FILE_NOT_FOUND":
                        self._record_file_race(repo_root=str(matched_root), relative_path=dest_relative, reason="file_not_found")
                        return
                    if self._handle_background_collection_error(exc=exc, phase="watcher_moved", worker_name="watcher"):
                        raise
                    return
            return
        try:
            self._index_file_with_priority(str(matched_root), relative_path, self._priority_high, "watcher")
        except CollectionError as exc:
            if exc.context.code == "ERR_FILE_NOT_FOUND":
                self._record_file_race(repo_root=str(matched_root), relative_path=relative_path, reason="file_not_found")
                return
            if self._handle_background_collection_error(exc=exc, phase="watcher_index", worker_name="watcher"):
                raise
            return

    def push_debounced_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """디바운스 버퍼에 이벤트를 적재한다."""
        source_path = Path(src_path).resolve()
        workspaces = self._workspace_repo.list_all()
        matched_root: Path | None = None
        for workspace in workspaces:
            root = Path(workspace.path).resolve()
            if _path_is_relative_to(source_path, root):
                matched_root = root
                break
        if matched_root is None:
            return
        relative_path = str(source_path.relative_to(matched_root).as_posix())
        key = (str(matched_root), relative_path)
        now_monotonic = self._now_monotonic()
        with self._debounce_lock:
            self._debounce_events[key] = (now_monotonic, event_type, dest_path)

    def flush_debounced_events(self) -> None:
        """디바운스 버퍼에서 만료된 이벤트를 처리한다."""
        due_items: list[tuple[str, str, str, str]] = []
        now_monotonic = self._now_monotonic()
        with self._debounce_lock:
            keys_to_delete: list[tuple[str, str]] = []
            for key, value in self._debounce_events.items():
                ts, event_type, dest_path = value
                if (now_monotonic - ts) * 1000.0 < float(self._watcher_debounce_ms()):
                    continue
                repo_root, relative_path = key
                src_path = str((Path(repo_root) / relative_path).resolve())
                due_items.append((event_type, src_path, dest_path, repo_root))
                keys_to_delete.append(key)
            for key in keys_to_delete:
                self._debounce_events.pop(key, None)
        for event_type, src_path, dest_path, _repo_root in due_items:
            self.handle_fs_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def _resolve_repo_root_for_path(self, path_value: Path) -> str | None:
        """입력 파일 경로가 속한 workspace root를 반환한다."""
        source_path = path_value.resolve()
        workspaces = self._workspace_repo.list_all()
        for workspace in workspaces:
            root = Path(workspace.path).resolve()
            if _path_is_relative_to(source_path, root):
                return str(root)
        return None

    def _record_file_race(self, repo_root: str, relative_path: str, reason: str) -> None:
        """경합으로 사라진 파일 이벤트를 저심각도 이벤트로 기록한다."""
        if self._on_watcher_file_race is None:
            return
        self._on_watcher_file_race(repo_root, relative_path, reason)


def _path_is_relative_to(path: Path, base: Path) -> bool:
    """path가 base 하위인지 판정한다."""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
