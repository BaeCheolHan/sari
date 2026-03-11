"""EventBus 기반 L5 비동기 품질 교정 watcher.

L3 flush 이벤트에 반응하여 L5 semantics가 없는 파일을 즉시 L5 queue에 enqueue한다.
LSP warm 완료 전까지는 이벤트를 수신하되 처리하지 않고 대기.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import UTC, datetime

from sari.core.event_bus import EventBus
from sari.core.events import L3FlushCompleted, LspWarmReady

log = logging.getLogger(__name__)


class L5AsyncUpgradeWatcher:
    """Phase 2: EventBus subscriber로 L3 flush에 즉시 반응하여 L5 enqueue.

    라이프사이클:
      1. start() → EventBus 구독 + watcher 스레드 시작
      2. LspWarmReady 수신 → _activated_repos에 repo_root 추가
      3. L3FlushCompleted 수신 → activated 상태이면 즉시 DB 조회 → batch enqueue
      4. timeout(poll_interval) → 주기적 DB 조회 (누락 방지 fallback)
      5. EventBus.shutdown() → loop 종료

    이전 L5AsyncQualityUpgradeJob과의 차이:
      - warm_delay_sec 제거 → 이벤트 기반 즉시 반응
      - 단일 trigger 후 종료 → 지속 감시 루프
      - watcher/scan 분기 불필요 → 모든 출처의 L3 flush를 동일하게 처리
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        enrich_queue_repo: object,
        tool_layer_repo: object,
        workspace_id: str,
        batch_size: int = 50,
        poll_interval_sec: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self._event_bus = event_bus
        self._enrich_queue_repo = enrich_queue_repo
        self._tool_layer_repo = tool_layer_repo
        self._workspace_id = str(workspace_id)
        self._batch_size = max(1, int(batch_size))
        self._poll_interval_sec = max(0.5, float(poll_interval_sec))
        self._enabled = bool(enabled)

        # 활성화된 repo_root 집합 (LspWarmReady 수신 시 추가)
        self._activated_repos: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._event_queue: queue.Queue[object] | None = None

    def start(self) -> None:
        """EventBus 구독 및 watcher 스레드 시작."""
        if not self._enabled:
            return
        self._event_queue = self._event_bus.subscribe_queue(
            [L3FlushCompleted, LspWarmReady],
        )
        self._thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="l5-upgrade-watcher",
        )
        self._thread.start()
        log.info("L5AsyncUpgradeWatcher started")

    def trigger_startup(self, *, repo_root: str) -> None:
        """daemon 재기동 시 미완료 L5 파일 감지.

        stale count > 0 이면 해당 repo_root를 즉시 활성화하고
        합성 이벤트를 발행하여 watcher loop를 깨운다.

        주의: tool_data_l4/l5 테이블에 language 컬럼이 없으므로
        repo_root 단위로만 stale count 조회.
        """
        if not self._enabled:
            return
        # workspace_id는 repo_root.strip()와 동일 (layer_upsert_builder._workspace_uid 준용)
        stale_count = self._tool_layer_repo.count_l5_stale(
            workspace_id=repo_root, repo_root=repo_root,
        )
        if stale_count > 0:
            log.info(
                "startup: %d stale L5 files detected (repo=%s)",
                stale_count, repo_root,
            )
            with self._lock:
                self._activated_repos.add(repo_root)
            # 합성 이벤트로 watcher loop 즉시 깨우기
            self._event_bus.publish(
                L3FlushCompleted(repo_root=repo_root, flushed_count=stale_count),
            )

    def _watch_loop(self) -> None:
        """메인 감시 루프. EventBus Queue에서 이벤트를 소비한다."""
        assert self._event_queue is not None
        while True:
            # 이벤트 대기 (timeout으로 주기적 fallback 보장)
            try:
                event = self._event_queue.get(timeout=self._poll_interval_sec)
            except queue.Empty:
                # timeout: activated repo에 대해 주기적 조회
                self._process_all_activated_repos()
                continue

            # 종료 감지
            if EventBus.is_sentinel(event):
                log.info("L5AsyncUpgradeWatcher received shutdown signal")
                break

            # 이벤트 처리
            if isinstance(event, LspWarmReady):
                with self._lock:
                    self._activated_repos.add(event.repo_root)
                log.info(
                    "L5 upgrade activated for repo=%s (language=%s)",
                    event.repo_root, event.language.value,
                )
                # 활성화 직후 즉시 처리 시도
                self._process_batch(repo_root=event.repo_root)
                continue

            if isinstance(event, L3FlushCompleted):
                with self._lock:
                    activated = event.repo_root in self._activated_repos
                if activated:
                    # drain: 짧은 시간 내 여러 flush 이벤트를 모아 한 번에 처리
                    self._drain_and_process(event.repo_root)
                # else: LSP 아직 미준비 → 무시 (warm 후 자동 처리됨)
                continue

    def _drain_and_process(self, repo_root: str) -> None:
        """Queue에 쌓인 이벤트를 drain 후 batch 처리."""
        assert self._event_queue is not None
        # 짧은 시간 내 추가 이벤트를 drain (batch 집계)
        drained = 0
        while True:
            try:
                extra = self._event_queue.get_nowait()
                if EventBus.is_sentinel(extra):
                    # shutdown 감지 시 이벤트를 다시 넣고 루프 종료 위임
                    self._event_queue.put(extra)
                    break
                if isinstance(extra, LspWarmReady):
                    with self._lock:
                        self._activated_repos.add(extra.repo_root)
                drained += 1
            except queue.Empty:
                break
        self._process_batch(repo_root=repo_root)

    def _process_all_activated_repos(self) -> None:
        """timeout 시 모든 활성 repo에 대해 처리 (fallback)."""
        with self._lock:
            repos = list(self._activated_repos)
        for repo_root in repos:
            self._process_batch(repo_root=repo_root)

    def _process_batch(self, *, repo_root: str) -> None:
        """DB에서 L5 semantics가 없는 파일을 조회하여 L5 queue에 enqueue."""
        now_iso = datetime.now(UTC).isoformat()
        try:
            # workspace_id는 repo_root.strip()와 동일 (layer_upsert_builder._workspace_uid 준용)
            files = self._tool_layer_repo.list_l5_upgrade_candidates(
                workspace_id=repo_root,
                repo_root=repo_root,
                limit=self._batch_size,
            )
        except (RuntimeError, TypeError, ValueError, OSError, AttributeError):
            log.exception("L5 upgrade query failed (repo=%s)", repo_root)
            return

        if not files:
            return

        enqueued = 0
        for file_dto in files:
            if self._is_l5_running(
                repo_root=str(file_dto["repo_root"]),
                relative_path=str(file_dto["relative_path"]),
                content_hash=str(file_dto["content_hash"]),
            ):
                continue
            try:
                self._enrich_queue_repo.enqueue(
                    repo_root=file_dto["repo_root"],
                    relative_path=file_dto["relative_path"],
                    content_hash=file_dto["content_hash"],
                    priority=20,
                    enqueue_source="l5",
                    now_iso=now_iso,
                )
                enqueued += 1
            except (RuntimeError, TypeError, ValueError, OSError):
                log.debug(
                    "L5 upgrade enqueue failed (path=%s)",
                    file_dto["relative_path"],
                )
        log.info(
            "L5 upgrade: enqueued %d / %d files (repo=%s)",
            enqueued, len(files), repo_root,
        )

    def _is_l5_running(self, *, repo_root: str, relative_path: str, content_hash: str) -> bool:
        """동일 파일의 L5 active 상태를 조회한다.

        - RUNNING: 경로 단위로 차단
        - PENDING: 현재 content_hash 일치 시만 차단
        """
        probe = getattr(self._enrich_queue_repo, "is_l5_job_running", None)
        if callable(probe):
            try:
                if bool(probe(repo_root=repo_root, relative_path=relative_path)):
                    return True
            except TypeError:
                try:
                    if bool(probe(repo_root=repo_root, relative_path=relative_path, content_hash=None)):
                        return True
                except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                    log.debug(
                        "L5 running probe failed (repo=%s, path=%s)",
                        repo_root,
                        relative_path,
                    )
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                log.debug(
                    "L5 running probe failed (repo=%s, path=%s)",
                    repo_root,
                    relative_path,
                )
        active_probe = getattr(self._enrich_queue_repo, "is_l5_job_active", None)
        if callable(active_probe):
            try:
                return bool(active_probe(repo_root=repo_root, relative_path=relative_path, content_hash=content_hash))
            except TypeError:
                try:
                    return bool(active_probe(repo_root=repo_root, relative_path=relative_path))
                except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                    log.debug(
                        "L5 active probe failed (repo=%s, path=%s)",
                        repo_root,
                        relative_path,
                    )
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                log.debug(
                    "L5 active probe failed (repo=%s, path=%s)",
                    repo_root,
                    relative_path,
                )
        return False
