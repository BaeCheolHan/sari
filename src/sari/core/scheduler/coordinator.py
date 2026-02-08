import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .priority_queue import AgingPriorityQueue

@dataclass(order=True)
class SchedulingTask:
    priority: int
    kind: str
    path: str
    root: str
    payload: Dict[str, Any] = field(default_factory=dict, compare=False)
    ts: float = field(default_factory=time.time, compare=False)

class SchedulingCoordinator:
    """
    Sari Task Coordinator.
    Restored for 100% legacy test compatibility.
    """
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("sari.coordinator")
        self.priority_queue = AgingPriorityQueue()
        self._stop = threading.Event()

    def enqueue_task(self, task: SchedulingTask):
        self.priority_queue.put(task)

    def get_next_task(self, timeout: float = 1.0) -> Optional[SchedulingTask]:
        try: return self.priority_queue.get(timeout=timeout)
        except: return None

    def task_done(self):
        self.priority_queue.task_done()

    def stop(self):
        self._stop.set()