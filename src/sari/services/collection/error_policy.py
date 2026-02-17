"""수집 파이프라인 오류 정책 전용 컴포넌트."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from sari.core.exceptions import CollectionError
from sari.core.models import now_iso8601_utc

log = logging.getLogger(__name__)


class CollectionErrorPolicy:
    """오류 기록/중단 정책/보존 정리를 담당한다."""

    def __init__(
        self,
        *,
        error_event_repo: object | None,
        run_mode: str,
        stop_background: Callable[[], None],
    ) -> None:
        """오류 정책 의존성을 주입받는다."""
        self._error_event_repo = error_event_repo
        self._run_mode = "prod" if run_mode == "prod" else "dev"
        self._stop_background = stop_background
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._last_error_at: str | None = None
        self._last_prune_epoch_sec = 0

    def last_error_code(self) -> str | None:
        """최근 오류 코드를 반환한다."""
        return self._last_error_code

    def last_error_message(self) -> str | None:
        """최근 오류 메시지를 반환한다."""
        return self._last_error_message

    def last_error_at(self) -> str | None:
        """최근 오류 시각을 반환한다."""
        return self._last_error_at

    def record_error_event(
        self,
        *,
        component: str,
        phase: str,
        severity: str,
        error_code: str,
        error_message: str,
        error_type: str,
        repo_root: str | None,
        relative_path: str | None,
        job_id: str | None,
        attempt_count: int,
        context_data: dict[str, object],
        worker_name: str = "collection",
        stacktrace_text: str | None = None,
    ) -> None:
        """오류 이벤트를 저장하고 최근 오류 상태를 갱신한다."""
        now_iso = now_iso8601_utc()
        self._last_error_code = error_code
        self._last_error_message = error_message
        self._last_error_at = now_iso
        if self._error_event_repo is None:
            return
        self._error_event_repo.record_event(
            occurred_at=now_iso,
            component=component,
            phase=phase,
            severity=severity,
            repo_root=repo_root,
            relative_path=relative_path,
            job_id=job_id,
            attempt_count=attempt_count,
            error_code=error_code,
            error_message=error_message,
            error_type=error_type,
            stacktrace_text="" if stacktrace_text is None else stacktrace_text,
            context_data=context_data,
            worker_name=worker_name,
            run_mode=self._run_mode,
        )

    def handle_background_collection_error(self, *, exc: CollectionError, phase: str, worker_name: str) -> bool:
        """백그라운드 오류 정책을 적용하고 중단 여부를 반환한다."""
        self.record_error_event(
            component="file_collection_service",
            phase=phase,
            severity="critical" if self._run_mode == "dev" else "error",
            error_code=exc.context.code,
            error_message=exc.context.message,
            error_type=type(exc).__name__,
            repo_root=None,
            relative_path=None,
            job_id=None,
            attempt_count=0,
            context_data={},
            worker_name=worker_name,
        )
        if self._run_mode == "dev":
            self._stop_background()
            log.error("수집 파이프라인 중지(run_mode=dev, phase=%s): %s", phase, exc.context.message)
            return True
        log.warning("수집 오류를 기록하고 계속 진행(run_mode=prod, phase=%s): %s", phase, exc.context.message)
        return False

    def prune_error_events_if_needed(self) -> None:
        """보존 기간이 지난 오류 이벤트를 주기적으로 정리한다."""
        if self._error_event_repo is None:
            return
        now_epoch = int(time.time())
        if now_epoch - self._last_prune_epoch_sec < 3600:
            return
        self._last_prune_epoch_sec = now_epoch
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        deleted = self._error_event_repo.prune(cutoff_iso=cutoff_iso, max_rows=200000)
        if deleted > 0:
            log.info("오류 이벤트 보존 정책 정리 완료(deleted=%s)", deleted)
