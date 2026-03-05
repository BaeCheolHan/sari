"""SQLite lock 재시도 유틸리티."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def run_with_sqlite_lock_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_backoff_sec: float = 0.05,
    max_backoff_sec: float = 0.4,
) -> tuple[T, int]:
    """database is locked 오류에 대해 지수 백오프 재시도를 수행한다."""
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        try:
            return operation(), attempt
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            if attempt + 1 >= attempts:
                raise
            sleep_sec = min(float(base_backoff_sec) * float(2**attempt), float(max_backoff_sec))
            time.sleep(max(0.0, sleep_sec))
    # for-loop 종료는 도달 불가지만 타입 안정성을 위해 명시한다.
    raise RuntimeError("sqlite retry loop exhausted unexpectedly")
