"""solidlsp에서 사용하는 로깅 유틸을 제공한다."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from logging import Logger


class LogTime(AbstractContextManager["LogTime"]):
    """컨텍스트 블록의 소요 시간을 로깅한다."""

    def __init__(self, message: str, logger: Logger) -> None:
        """로그 메시지와 로거를 저장한다."""
        self._message = message
        self._logger = logger
        self._start = 0.0

    def __enter__(self) -> "LogTime":
        """진입 시각을 기록한다."""
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:  # type: ignore[override]
        """종료 시 소요 시간을 기록한다."""
        elapsed = time.perf_counter() - self._start
        self._logger.info("%s took %.3fs", self._message, elapsed)
        return None
