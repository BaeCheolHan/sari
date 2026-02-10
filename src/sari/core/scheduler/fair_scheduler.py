import time
import heapq
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


@dataclass(order=True)
class ScheduledTask:
    priority: float  # Lower value = Higher priority
    timestamp: float = field(compare=False)
    root_id: str = field(compare=False)
    payload: Any = field(compare=False)


class WeightedFairQueue:
    """
    Combines Weighted Fair Queueing (WFQ) with Aging.
    Ensures fairness between roots AND prevents starvation within a root.
    """

    def __init__(self, age_factor: float = 0.05):
        self._queues: Dict[str, List[ScheduledTask]] = {}  # Min-heaps per root
        self._weights: Dict[str, float] = {}
        self._active_roots: List[str] = []
        self._current_idx = 0
        self._age_factor = age_factor
        self._lock = __import__("threading").Lock()

    def set_weight(self, root_id: str, weight: float):
        with self._lock:
            self._weights[root_id] = weight

    def put(self, root_id: str, task: Any, base_priority: float = 10.0):
        with self._lock:
            if root_id not in self._queues:
                self._queues[root_id] = []
                self._active_roots.append(root_id)

            stask = ScheduledTask(
                priority=base_priority / self._weights.get(root_id, 1.0),
                timestamp=time.time(),
                root_id=root_id,
                payload=task
            )
            heapq.heappush(self._queues[root_id], stask)

    def get(self) -> Optional[ScheduledTask]:
        """Weighted Round-Robin + Internal Aging."""
        with self._lock:
            if not self._active_roots:
                return None

            start_idx = self._current_idx
            while True:
                root_id = self._active_roots[self._current_idx]
                q = self._queues[root_id]

                if q:
                    # Apply aging to all elements in this root's queue
                    # (Simplified: we age the head and then pop it)
                    now = time.time()
                    for task in q:
                        wait_time = now - task.timestamp
                        task.priority -= wait_time * self._age_factor

                    heapq.heapify(q)
                    task = heapq.heappop(q)
                    self._current_idx = (
                        self._current_idx + 1) % len(self._active_roots)
                    return task

                self._current_idx = (self._current_idx +
                                     1) % len(self._active_roots)
                if self._current_idx == start_idx:
                    break
            return None

    def qsize(self) -> int:
        return sum(len(q) for q in self._queues.values())
