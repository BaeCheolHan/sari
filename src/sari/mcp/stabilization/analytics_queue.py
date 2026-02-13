from __future__ import annotations

import queue
import threading
from collections import deque
from typing import Mapping


class AnalyticsQueue:
    def __init__(self, *, maxsize: int = 2000, max_drop_types: int = 128):
        self._queue: "queue.Queue[dict[str, object]]" = queue.Queue(maxsize=maxsize)
        self._max_drop_types = max(1, int(max_drop_types))
        self._drop_count_by_type: dict[str, int] = {}
        self._drop_type_order: deque[str] = deque()
        self._lock = threading.RLock()

    def enqueue(self, event: Mapping[str, object]) -> bool:
        event_type = str(event.get("event_type") or "unknown")
        try:
            self._queue.put_nowait(dict(event))
            return True
        except queue.Full:
            with self._lock:
                if event_type not in self._drop_count_by_type:
                    while len(self._drop_count_by_type) >= self._max_drop_types and self._drop_type_order:
                        oldest = self._drop_type_order.popleft()
                        self._drop_count_by_type.pop(oldest, None)
                    self._drop_type_order.append(event_type)
                self._drop_count_by_type[event_type] = self._drop_count_by_type.get(event_type, 0) + 1
            return False

    def drain(self, *, limit: int = 1000) -> list[dict[str, object]]:
        drained: list[dict[str, object]] = []
        for _ in range(max(0, int(limit))):
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def drop_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._drop_count_by_type)


_QUEUE = AnalyticsQueue()


def enqueue_analytics(event: Mapping[str, object]) -> bool:
    return _QUEUE.enqueue(event)


def drain_analytics(*, limit: int = 1000) -> list[dict[str, object]]:
    return _QUEUE.drain(limit=limit)


def analytics_drop_counts() -> dict[str, int]:
    return _QUEUE.drop_counts()


def reset_analytics_queue_for_tests() -> None:
    global _QUEUE
    _QUEUE = AnalyticsQueue()
