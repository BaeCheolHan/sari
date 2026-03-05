"""스캔 계열 운영 명령의 배타 제어 및 재시도를 제공한다."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from sari.core.exceptions import CollectionError, ErrorContext

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - non-posix fallback
    fcntl = None


class ScanOperationLock:
    """프로세스 간 scan/index_file 실행을 직렬화한다."""

    def __init__(
        self,
        *,
        lock_path: Path,
        max_attempts: int = 6,
        backoff_base_sec: float = 0.05,
        backoff_max_sec: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
        lock_fn: Callable[[object], None] | None = None,
        unlock_fn: Callable[[object], None] | None = None,
    ) -> None:
        self._lock_path = lock_path
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base_sec = max(0.0, float(backoff_base_sec))
        self._backoff_max_sec = max(self._backoff_base_sec, float(backoff_max_sec))
        self._sleep = sleep_fn
        self._lock_fn = lock_fn if lock_fn is not None else self._default_lock
        self._unlock_fn = unlock_fn if unlock_fn is not None else self._default_unlock

    @contextmanager
    def acquire(self, *, operation: str, repo_root: str):
        """락 획득 시도 후 성공 시 임계구역을 실행한다."""
        if fcntl is None:
            yield
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._lock_path.open("a+", encoding="utf-8")
        acquired = False
        try:
            for attempt in range(self._max_attempts):
                try:
                    self._lock_fn(lock_file)
                    acquired = True
                    break
                except BlockingIOError:
                    if attempt + 1 >= self._max_attempts:
                        raise CollectionError(
                            ErrorContext(
                                code="ERR_SCAN_OPERATION_LOCK_BUSY",
                                message=f"scan operation lock busy(operation={operation}, repo={repo_root})",
                            )
                        )
                    sleep_sec = min(self._backoff_base_sec * float(2**attempt), self._backoff_max_sec)
                    self._sleep(sleep_sec)
            yield
        finally:
            if acquired:
                self._unlock_fn(lock_file)
            lock_file.close()

    @staticmethod
    def _default_lock(lock_file: object) -> None:
        if fcntl is None:
            return
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _default_unlock(lock_file: object) -> None:
        if fcntl is None:
            return
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
