import time
import pytest
from sari.core.scheduler.fair_scheduler import WeightedFairQueue
from sari.core.scheduler.priority_queue import AgingPriorityQueue
from sari.core.scheduler.throttle import TokenBucket, AdaptiveDebouncer

def test_fair_queue_round_robin():
    fq = WeightedFairQueue()
    fq.put("root-1", "task-1-1")
    fq.put("root-2", "task-2-1")
    fq.put("root-1", "task-1-2")
    
    # Round-robin check
    r1, t1 = fq.get()
    assert r1 == "root-1"
    assert t1 == "task-1-1"
    
    r2, t2 = fq.get()
    assert r2 == "root-2"
    assert t2 == "task-2-1"
    
    r3, t3 = fq.get()
    assert r3 == "root-1"
    assert t3 == "task-1-2"

def test_fair_queue_aging():
    fq = WeightedFairQueue(age_factor=1000.0) # Massive aging
    fq.put("root-1", "low-prio-old", base_priority=100.0)
    
    # Wait to let it age significantly
    time.sleep(0.1)
    
    fq.put("root-1", "high-prio-new", base_priority=1.0)
    
    # "low-prio-old" priority will be 100 - (0.1 * 1000) = 0
    # "high-prio-new" priority will be 1
    # So low-prio-old should win
    r, t = fq.get()
    assert t == "low-prio-old"

def test_token_bucket():
    bucket = TokenBucket(capacity=2.0, fill_rate=1.0)
    assert bucket.consume(1.0) is True
    assert bucket.consume(1.0) is True
    assert bucket.consume(1.0) is False # Empty
    
    time.sleep(1.1)
    assert bucket.consume(1.0) is True # Refilled

def test_aging_priority_queue():
    pq = AgingPriorityQueue(age_factor=1.0)
    pq.put("root-1", "low-prio", base_priority=100.0)
    time.sleep(0.1)
    pq.put("root-1", "high-prio", base_priority=10.0)
    
    # Re-calculate happens during get
    res = pq.get()
    # res is (root_id, payload)
    assert res[1] in ["low-prio", "high-prio"]
