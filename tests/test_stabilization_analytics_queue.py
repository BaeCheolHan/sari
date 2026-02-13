from sari.mcp.stabilization.analytics_queue import AnalyticsQueue


def test_analytics_queue_drops_newest_when_full():
    q = AnalyticsQueue(maxsize=2)
    assert q.enqueue({"event_type": "search", "v": 1}) is True
    assert q.enqueue({"event_type": "search", "v": 2}) is True
    assert q.enqueue({"event_type": "search", "v": 3}) is False
    assert q.drop_counts()["search"] == 1
    drained = q.drain(limit=10)
    assert len(drained) == 2


def test_analytics_queue_drain_preserves_fifo_order():
    q = AnalyticsQueue(maxsize=10)
    assert q.enqueue({"event_type": "search", "v": 1}) is True
    assert q.enqueue({"event_type": "read", "v": 2}) is True
    assert q.enqueue({"event_type": "read", "v": 3}) is True
    drained = q.drain(limit=2)
    assert [d["v"] for d in drained] == [1, 2]
    remainder = q.drain(limit=10)
    assert [d["v"] for d in remainder] == [3]


def test_analytics_queue_drop_counts_are_bounded_by_type():
    q = AnalyticsQueue(maxsize=1, max_drop_types=2)
    assert q.enqueue({"event_type": "seed", "v": 0}) is True

    assert q.enqueue({"event_type": "a"}) is False
    assert q.enqueue({"event_type": "b"}) is False
    assert q.enqueue({"event_type": "c"}) is False

    counts = q.drop_counts()
    assert len(counts) == 2
    assert "c" in counts
