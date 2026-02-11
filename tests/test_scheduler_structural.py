from sari.core.scheduler.fair_scheduler import ScheduledTask, WeightedFairQueue
from sari.core.scheduler.priority_queue import AgingPriorityQueue, PrioritizedTask


def test_aging_priority_queue_get_returns_prioritized_task():
    q = AgingPriorityQueue()
    q.put("root-a", {"path": "a.py"}, base_priority=5.0)

    task = q.get()

    assert isinstance(task, PrioritizedTask)
    assert task is not None
    assert task.root_id == "root-a"
    assert task.payload == {"path": "a.py"}


def test_weighted_fair_queue_get_returns_scheduled_task():
    q = WeightedFairQueue()
    q.set_weight("root-b", 1.0)
    q.put("root-b", {"path": "b.py"}, base_priority=3.0)

    task = q.get()

    assert isinstance(task, ScheduledTask)
    assert task is not None
    assert task.root_id == "root-b"
    assert task.payload == {"path": "b.py"}


def test_weighted_fair_queue_non_positive_weight_does_not_crash():
    q = WeightedFairQueue()
    q.set_weight("root-c", 0.0)
    q.put("root-c", {"path": "c.py"}, base_priority=4.0)

    task = q.get()

    assert task is not None
    assert task.root_id == "root-c"
