"""stabilization analytics queue 동작을 검증한다."""

from __future__ import annotations

from sari.mcp.stabilization.analytics_queue import (
    analytics_drop_counts,
    drain_analytics,
    enqueue_analytics,
    reset_analytics_queue_for_tests,
)


def test_analytics_queue_tracks_drop_counts_on_overflow() -> None:
    """큐 용량 초과 시 이벤트 타입별 드롭 카운트를 누적해야 한다."""
    reset_analytics_queue_for_tests()
    inserted = 0
    for index in range(2500):
        accepted = enqueue_analytics({"event_type": "read", "seq": index})
        if accepted:
            inserted += 1
    drained = drain_analytics(limit=3000)
    assert len(drained) == inserted
    drops = analytics_drop_counts()
    assert drops.get("read", 0) >= 1

