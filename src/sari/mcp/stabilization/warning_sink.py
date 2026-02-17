"""stabilization 경고 sink를 제공한다."""

from __future__ import annotations

import threading
from collections import deque


class WarningSink:
    """최근 경고 메시지를 보관하는 경량 sink다."""

    def __init__(self, max_items: int = 512) -> None:
        """보관 상한을 설정한다."""
        self._max_items = max(1, int(max_items))
        self._items: deque[str] = deque()
        self._lock = threading.RLock()

    def add(self, message: str) -> None:
        """경고 메시지를 추가한다."""
        with self._lock:
            self._items.append(str(message))
            while len(self._items) > self._max_items:
                self._items.popleft()

    def list_recent(self, limit: int = 50) -> list[str]:
        """최근 경고 목록을 반환한다."""
        with self._lock:
            sliced = list(self._items)[-max(1, int(limit)) :]
        return sliced


warning_sink = WarningSink()


def warn(message: str) -> None:
    """전역 warning sink에 메시지를 기록한다."""
    warning_sink.add(message)

