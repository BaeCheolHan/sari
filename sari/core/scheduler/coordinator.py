import threading
import time
from typing import Any, Optional
from .priority_queue import AgingPriorityQueue
from .fair_scheduler import WeightedFairQueue

class SchedulingCoordinator:
    """
    Orchestrates indexing vs search priorities.
    """
    def __init__(self):
        self.priority_queue = AgingPriorityQueue()
        self.fair_queue = WeightedFairQueue()
        self._is_searching = threading.Event()
        self._last_search_ts = 0.0
        self._search_grace_period = 2.0 # Seconds to throttle after search
        self._priority_burst = 0
        self._max_priority_burst = 5

    def enqueue_fair(self, root_id: str, task: Any, base_priority: float = 10.0) -> None:
        self.fair_queue.put(root_id, task, base_priority=base_priority)

    def enqueue_priority(self, root_id: str, task: Any, base_priority: float = 1.0) -> None:
        self.priority_queue.put(root_id, task, base_priority=base_priority)

    def get_next_task(self) -> Optional[tuple]:
        """
        Priority queue first, but avoid starving fair queue.
        Returns (root_id, task) or None.
        """
        if self.priority_queue.qsize() > 0:
            if self._priority_burst < self._max_priority_burst or self.fair_queue.qsize() == 0:
                self._priority_burst += 1
                return self.priority_queue.get()
        self._priority_burst = 0
        return self.fair_queue.get()

    def notify_search_start(self):
        self._is_searching.set()
        self._last_search_ts = time.time()

    def notify_search_end(self):
        # We don't clear immediately to allow for rapid follow-up searches
        pass

    def should_throttle_indexing(self) -> bool:
        """Check if we should slow down indexing due to active search."""
        if self._is_searching.is_set():
            if time.time() - self._last_search_ts > self._search_grace_period:
                self._is_searching.clear()
                return False
            return True
        return False

    def get_sleep_penalty(self) -> float:
        """Return recommended sleep time for workers under read pressure."""
        if self.should_throttle_indexing():
            return 0.5 # 500ms penalty between tasks
        return 0.0
