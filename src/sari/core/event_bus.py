"""범용 EventBus — pub/sub 이벤트 브로커.

두 가지 구독 방식을 지원한다:
  1. subscribe(event_type, handler) — 콜백 기반. publisher 스레드에서 동기 호출.
  2. subscribe_queue(event_types) — Queue 반환. subscriber가 자체 스레드에서 소비.

사용 예시:
    bus = EventBus()

    # 콜백 구독
    bus.subscribe(LspWarmReady, lambda e: print(e))

    # Queue 구독 (여러 이벤트 타입 가능)
    q = bus.subscribe_queue([L3FlushCompleted, LspWarmReady])
    event = q.get(timeout=5.0)  # blocking

    # 발행
    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=10))

    # 종료
    bus.shutdown()  # 모든 queue에 sentinel 전달
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# 내부 종료 마커 — is_sentinel()로 식별
_SENTINEL = object()


class EventBus:
    """thread-safe 범용 EventBus."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self._queues: dict[type, list[queue.Queue[Any]]] = defaultdict(list)
        self._shutdown = False

    def subscribe(self, event_type: type[T], handler: Callable[[T], None]) -> None:
        """콜백 기반 구독. publisher 스레드에서 동기 호출된다."""
        with self._lock:
            self._handlers[event_type].append(handler)

    def subscribe_queue(
        self,
        event_types: list[type],
        *,
        maxsize: int = 0,
    ) -> queue.Queue[Any]:
        """Queue 기반 구독. 여러 이벤트 타입을 하나의 Queue로 수신.

        subscriber는 자체 스레드에서 queue.get(timeout=...)으로 소비.
        shutdown() 시 _SENTINEL이 주입되므로 is_sentinel()로 종료 감지.

        Args:
            event_types: 구독할 이벤트 타입 리스트
            maxsize: Queue 최대 크기 (0=무제한)

        Returns:
            이벤트가 들어오는 Queue 인스턴스
        """
        q: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        with self._lock:
            for et in event_types:
                self._queues[et].append(q)
        return q

    def publish(self, event: object) -> None:
        """이벤트 발행. 등록된 콜백 호출 + Queue에 put.

        콜백에서 발생하는 예외는 로깅 후 무시 (publisher를 블로킹하지 않음).
        Queue가 가득 찬 경우 put_nowait 실패를 로깅 후 무시.
        """
        if self._shutdown:
            return
        event_type = type(event)
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
            queues = list(self._queues.get(event_type, []))

        for handler in handlers:
            try:
                handler(event)
            except (RuntimeError, TypeError, ValueError, AttributeError, KeyError, OSError):
                log.exception("EventBus handler error for %s", event_type.__name__)

        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.warning("EventBus queue full, dropping %s", event_type.__name__)

    def shutdown(self) -> None:
        """모든 Queue 구독자에게 종료 신호를 보낸다."""
        self._shutdown = True
        with self._lock:
            all_queues: set[queue.Queue[Any]] = set()
            for qs in self._queues.values():
                all_queues.update(qs)
        for q in all_queues:
            try:
                q.put_nowait(_SENTINEL)
            except queue.Full:
                log.debug("EventBus shutdown: queue full, sentinel dropped")

    @staticmethod
    def is_sentinel(event: object) -> bool:
        """Queue에서 꺼낸 이벤트가 종료 마커인지 확인."""
        return event is _SENTINEL
