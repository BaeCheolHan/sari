"""수집 런타임 생명주기 전용 컴포넌트."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.observers import Observer

from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.models import now_iso8601_utc


class RuntimeManager:
    """scheduler/enrich/watcher 루프 생명주기를 관리한다."""

    def __init__(
        self,
        *,
        stop_event: threading.Event,
        enrich_queue_repo: object,
        workspace_repo: object,
        policy: object,
        policy_repo: object | None,
        assert_parent_alive: Callable[[str], None],
        scan_once: Callable[[str], object],
        process_enrich_jobs_bootstrap: Callable[[int], int],
        handle_background_collection_error: Callable[[CollectionError, str, str], bool],
        prune_error_events_if_needed: Callable[[], None],
        watcher_loop: Callable[[], None],
    ) -> None:
        """런타임 루프 구성요소를 주입받는다."""
        self._stop_event = stop_event
        self._enrich_queue_repo = enrich_queue_repo
        self._workspace_repo = workspace_repo
        self._policy = policy
        self._policy_repo = policy_repo
        self._assert_parent_alive = assert_parent_alive
        self._scan_once = scan_once
        self._process_enrich_jobs_bootstrap = process_enrich_jobs_bootstrap
        self._handle_background_collection_error = handle_background_collection_error
        self._prune_error_events_if_needed = prune_error_events_if_needed
        self._watcher_loop = watcher_loop
        self._scheduler_thread: threading.Thread | None = None
        self._enrich_threads: list[threading.Thread] = []
        self._watcher_thread: threading.Thread | None = None
        self._observer: Observer | None = None

    def set_observer(self, observer: Observer | None) -> None:
        """watcher에서 생성한 observer를 저장한다."""
        self._observer = observer

    def enrich_thread_count(self) -> int:
        """실행 중 enrich 스레드 수를 반환한다."""
        return len(self._enrich_threads)

    def stop_signal(self) -> None:
        """내부 stop 이벤트를 활성화한다."""
        self._stop_event.set()

    def start_background(self) -> None:
        """백그라운드 루프를 시작한다."""
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._enrich_queue_repo.reset_running_to_failed(now_iso=now_iso8601_utc())
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._enrich_threads = []
        self._watcher_thread = threading.Thread(target=self._watcher_loop, daemon=True)
        worker_count = 1
        if self._policy_repo is not None:
            worker_count = self._policy_repo.get_policy().enrich_worker_count
        for _ in range(max(1, worker_count)):
            self._enrich_threads.append(threading.Thread(target=self._enrich_loop, daemon=True))
        self._scheduler_thread.start()
        for thread in self._enrich_threads:
            thread.start()
        self._watcher_thread.start()

    def stop_background(self) -> None:
        """백그라운드 루프를 정지한다."""
        self._stop_event.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=2.0)
        for thread in self._enrich_threads:
            thread.join(timeout=2.0)
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=2.0)

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            self._assert_parent_alive("scheduler")
            start_time = time.time()
            try:
                workspaces = self._workspace_repo.list_all()
            except sqlite3.Error as exc:
                fatal_error = CollectionError(
                    ErrorContext(
                        code="ERR_COLLECTION_DB_FATAL",
                        message=f"workspace 조회 실패: {exc}",
                    )
                )
                if self._handle_background_collection_error(fatal_error, "scheduler_workspace_list", "scheduler"):
                    return
                continue
            for workspace in workspaces:
                if not workspace.is_active:
                    continue
                try:
                    self._scan_once(workspace.path)
                except CollectionError as exc:
                    if not self._handle_background_collection_error(exc, "scheduler_scan", "scheduler"):
                        continue
                    return
            try:
                self._prune_error_events_if_needed()
            except sqlite3.Error as exc:
                fatal_error = CollectionError(
                    ErrorContext(
                        code="ERR_COLLECTION_DB_FATAL",
                        message=f"오류 이벤트 정리 실패: {exc}",
                    )
                )
                if self._handle_background_collection_error(fatal_error, "scheduler_prune", "scheduler"):
                    return
                continue
            elapsed = time.time() - start_time
            remain = max(0.0, float(self._policy.scan_interval_sec) - elapsed)
            if remain > 0:
                self._stop_event.wait(timeout=remain)

    def _enrich_loop(self) -> None:
        while not self._stop_event.is_set():
            self._assert_parent_alive("enrich_worker")
            try:
                processed = self._process_enrich_jobs_bootstrap(int(self._policy.max_enrich_batch))
            except CollectionError as exc:
                if self._handle_background_collection_error(exc, "enrich_loop", "enrich_worker"):
                    return
                processed = 0
            except sqlite3.Error as exc:
                fatal_error = CollectionError(
                    ErrorContext(
                        code="ERR_COLLECTION_DB_FATAL",
                        message=f"enrich 처리 실패: {exc}",
                    )
                )
                if self._handle_background_collection_error(fatal_error, "enrich_loop_db", "enrich_worker"):
                    return
                processed = 0
            if processed == 0:
                self._stop_event.wait(timeout=float(self._policy.queue_poll_interval_ms) / 1000.0)
