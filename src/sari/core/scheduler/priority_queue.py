import heapq
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

@dataclass(order=True)
class PrioritizedTask:
    priority: float  # Lower value = Higher priority
    timestamp: float = field(compare=False)
    root_id: str = field(compare=False)
    payload: Any = field(compare=False)
    base_priority: float = field(compare=False, default=10.0)

class AgingPriorityQueue:
    """
    Priority queue with aging to prevent starvation.
    Priority = base_priority - (current_time - wait_start_time) * age_factor
    """
    def __init__(self, age_factor: float = 0.1):
        self._queue: List[PrioritizedTask] = []
        self._age_factor = age_factor
        self._lock = __import__("threading").Lock()

    def put(self, root_id: str, payload: Any, base_priority: float = 10.0):
        with self._lock:
            now = time.time()
            task = PrioritizedTask(
                priority=base_priority,
                timestamp=now,
                root_id=root_id,
                payload=payload,
                base_priority=base_priority
            )
            heapq.heappush(self._queue, task)

    def get(self) -> Optional[PrioritizedTask]:
        with self._lock:
            if not self._queue:
                return None
            
            # Re-calculate priorities with aging (every N gets or on timeout)
            # For simplicity in this local tool, we update on every get if needed
            self._apply_aging()
            
            return heapq.heappop(self._queue)

    def _apply_aging(self):
        """Adjust priorities based on wait time."""
        now = time.time()
        new_queue = []
        for task in self._queue:
            wait_time = now - task.timestamp
            # Lower value is higher priority in heapq
            task.priority = task.base_priority - (wait_time * self._age_factor)
            new_queue.append(task)
        
        heapq.heapify(new_queue)
        self._queue = new_queue

    def qsize(self) -> int:
        return len(self._queue)
