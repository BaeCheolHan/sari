"""stabilization 분석 이벤트 큐를 제공한다."""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Mapping


class AnalyticsQueue:
    """분석 이벤트를 비동기 수집하는 in-memory 큐다."""

    def __init__(self, maxsize: int = 2000, max_drop_types: int = 128) -> None:
        """큐 용량과 드롭 카운트 추적 상한을 초기화한다."""
        self._queue: queue.Queue[dict[str, object]] = queue.Queue(maxsize=maxsize)
        self._max_drop_types = max(1, int(max_drop_types))
        self._drop_count_by_type: dict[str, int] = {}
        self._drop_type_order: deque[str] = deque()
        self._lock = threading.RLock()

    def enqueue(self, event: Mapping[str, object]) -> bool:
        """이벤트를 큐에 삽입하고 성공 여부를 반환한다."""
        event_type = str(event.get("event_type") or "unknown")
        try:
            self._queue.put_nowait(dict(event))
            return True
        except queue.Full:
            with self._lock:
                if event_type not in self._drop_count_by_type:
                    while len(self._drop_count_by_type) >= self._max_drop_types and len(self._drop_type_order) > 0:
                        oldest = self._drop_type_order.popleft()
                        self._drop_count_by_type.pop(oldest, None)
                    self._drop_type_order.append(event_type)
                self._drop_count_by_type[event_type] = self._drop_count_by_type.get(event_type, 0) + 1
            return False

    def drain(self, limit: int = 1000) -> list[dict[str, object]]:
        """큐에서 최대 limit 개를 배출한다."""
        drained: list[dict[str, object]] = []
        for _ in range(max(0, int(limit))):
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def drop_counts(self) -> dict[str, int]:
        """이벤트 타입별 드롭 횟수를 반환한다."""
        with self._lock:
            return dict(self._drop_count_by_type)


_QUEUE = AnalyticsQueue()


def enqueue_analytics(event: Mapping[str, object]) -> bool:
    """전역 분석 큐에 이벤트를 추가한다."""
    return _QUEUE.enqueue(event)


def drain_analytics(limit: int = 1000) -> list[dict[str, object]]:
    """전역 분석 큐에서 이벤트를 배출한다."""
    return _QUEUE.drain(limit=limit)


def analytics_drop_counts() -> dict[str, int]:
    """전역 분석 큐의 드롭 카운트를 반환한다."""
    return _QUEUE.drop_counts()


def reset_analytics_queue_for_tests() -> None:
    """테스트를 위해 전역 큐 상태를 초기화한다."""
    global _QUEUE
    _QUEUE = AnalyticsQueue()

