"""EventBus 단위 테스트."""

from __future__ import annotations

import queue
import threading

import pytest

from sari.core.event_bus import EventBus
from sari.core.events import L3FlushCompleted, LspWarmReady


# ---------------------------------------------------------------------------
# 콜백(subscribe) 테스트
# ---------------------------------------------------------------------------

def test_publish_calls_subscriber_callback() -> None:
    """publish() 는 등록된 콜백을 동기 호출한다."""
    bus = EventBus()
    received: list[object] = []
    bus.subscribe(L3FlushCompleted, received.append)

    event = L3FlushCompleted(repo_root="/repo", flushed_count=5)
    bus.publish(event)

    assert received == [event]


def test_callback_exception_does_not_block_other_handlers() -> None:
    """콜백에서 예외가 발생해도 나머지 콜백은 호출된다."""
    bus = EventBus()
    second_called: list[bool] = []

    bus.subscribe(L3FlushCompleted, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(L3FlushCompleted, lambda _: second_called.append(True))

    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=1))
    assert second_called == [True]


def test_callback_assertion_error_does_not_block_other_handlers() -> None:
    """AssertionError 같은 일반 Exception도 삼키고 다음 핸들러를 실행해야 한다."""
    bus = EventBus()
    second_called: list[bool] = []

    bus.subscribe(L3FlushCompleted, lambda _: (_ for _ in ()).throw(AssertionError("boom")))
    bus.subscribe(L3FlushCompleted, lambda _: second_called.append(True))

    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=1))
    assert second_called == [True]


def test_unregistered_event_type_does_not_raise() -> None:
    """구독자가 없는 이벤트 타입 발행이 예외를 던지지 않는다."""
    bus = EventBus()
    bus.publish(L3FlushCompleted(repo_root="/repo", flushed_count=0))  # 구독자 없음


# ---------------------------------------------------------------------------
# Queue(subscribe_queue) 테스트
# ---------------------------------------------------------------------------

def test_publish_puts_event_in_subscribed_queue() -> None:
    """publish() 는 subscribe_queue()로 반환된 Queue에 이벤트를 넣는다."""
    bus = EventBus()
    q = bus.subscribe_queue([L3FlushCompleted])

    event = L3FlushCompleted(repo_root="/repo", flushed_count=3)
    bus.publish(event)

    received = q.get_nowait()
    assert received is event


def test_subscribe_queue_multiple_event_types() -> None:
    """하나의 Queue로 여러 이벤트 타입을 수신한다."""
    bus = EventBus()
    from solidlsp.ls_config import Language
    q = bus.subscribe_queue([L3FlushCompleted, LspWarmReady])

    e1 = L3FlushCompleted(repo_root="/r", flushed_count=1)
    e2 = LspWarmReady(repo_root="/r", language=Language.PYTHON)
    bus.publish(e1)
    bus.publish(e2)

    assert q.get_nowait() is e1
    assert q.get_nowait() is e2


def test_subscribe_queue_with_maxsize_drops_on_full() -> None:
    """maxsize 초과 시 이벤트가 드롭되고 예외가 발생하지 않는다."""
    bus = EventBus()
    q = bus.subscribe_queue([L3FlushCompleted], maxsize=1)

    # 첫 번째는 들어가고 두 번째는 드롭
    bus.publish(L3FlushCompleted(repo_root="/r", flushed_count=1))
    bus.publish(L3FlushCompleted(repo_root="/r", flushed_count=2))

    first = q.get_nowait()
    assert first.flushed_count == 1
    with pytest.raises(queue.Empty):
        q.get_nowait()


# ---------------------------------------------------------------------------
# shutdown 테스트
# ---------------------------------------------------------------------------

def test_shutdown_delivers_sentinel_to_all_queues() -> None:
    """shutdown() 이후 모든 Queue에 sentinel이 전달된다."""
    bus = EventBus()
    q1 = bus.subscribe_queue([L3FlushCompleted])
    q2 = bus.subscribe_queue([LspWarmReady])

    bus.shutdown()

    assert EventBus.is_sentinel(q1.get_nowait())
    assert EventBus.is_sentinel(q2.get_nowait())


def test_publish_after_shutdown_is_ignored() -> None:
    """shutdown() 이후 publish()는 무시된다."""
    bus = EventBus()
    q = bus.subscribe_queue([L3FlushCompleted])
    bus.shutdown()
    _ = q.get_nowait()  # sentinel 소비

    bus.publish(L3FlushCompleted(repo_root="/r", flushed_count=1))

    with pytest.raises(queue.Empty):
        q.get_nowait()


def test_is_sentinel_returns_false_for_normal_event() -> None:
    """일반 이벤트 객체에 대해 is_sentinel()은 False를 반환한다."""
    assert not EventBus.is_sentinel(L3FlushCompleted(repo_root="/r", flushed_count=0))
    assert not EventBus.is_sentinel(None)
    assert not EventBus.is_sentinel("string")


# ---------------------------------------------------------------------------
# thread safety 기본 검증
# ---------------------------------------------------------------------------

def test_concurrent_publish_does_not_raise() -> None:
    """여러 스레드에서 동시에 publish해도 예외가 발생하지 않는다."""
    bus = EventBus()
    results: list[object] = []
    lock = threading.Lock()
    bus.subscribe(L3FlushCompleted, lambda e: (lock.acquire(), results.append(e), lock.release()))

    threads = [
        threading.Thread(
            target=bus.publish,
            args=(L3FlushCompleted(repo_root="/r", flushed_count=i),),
        )
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 20
