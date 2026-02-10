import queue
import threading
from typing import Generic, Hashable, List, Optional, Set, TypeVar
from sari.core.settings import settings

T = TypeVar("T", bound=Hashable)


class DedupQueue(Generic[T]):
    """
    A thread-safe queue that ignores duplicate items currently pending control.
    """
    def __init__(self, maxsize: Optional[int] = None):
        # Default to 5000 if not provided
        self.maxsize = maxsize or settings.get_int("INDEX_QUEUE_SIZE", 5000)
        self.q: "queue.Queue[T]" = queue.Queue(maxsize=self.maxsize)
        self.pending: Set[T] = set()
        self.lock = threading.Lock()

    def put(self, item: T) -> bool:
        """
        Put item into queue. Returns True if added, False if already pending.
        """
        with self.lock:
            if item in self.pending:
                return False
            self.pending.add(item)
            self.q.put(item)
            return True

    def get(self, block: bool = True, timeout: Optional[float] = None) -> T:
        try:
            item = self.q.get(block=block, timeout=timeout)
            return item
        except queue.Empty:
            raise

    def task_done(self, item: T) -> None:
        """
        Mark item as processed, removing it from pending set.
        """
        with self.lock:
            self.pending.discard(item)
        self.q.task_done()

    def get_batch(self, max_size: int = 50, timeout: float = 0.1) -> List[T]:
        """
        Get up to max_size items.
        Note: You must assume ownership of these items.
        We remove them from 'pending' set when we return them??
        Wait, usually 'task_done' is called after processing.
        But for 'Dedup', if we pull it out, it's no longer 'pending in queue',
        so we should allow re-queueing (e.g. if file changes again while we process).
        So removing from 'pending' set immediately upon 'get' is correct for ensuring
        "If it changes AGAIN, we queue it AGAIN".
        """
        items = []
        try:
            # Blocking get for first item
            item = self.q.get(block=True, timeout=timeout)
            items.append(item)
            # Remove from pending immediately so new events can be queued
            with self.lock:
                self.pending.discard(item)
            self.q.task_done() # We count 'task_done' regarding the queue generic logic

            # Non-blocking for rest
            while len(items) < max_size:
                try:
                    item = self.q.get_nowait()
                    items.append(item)
                    with self.lock:
                        self.pending.discard(item)
                    self.q.task_done()
                except queue.Empty:
                    break
        except queue.Empty:
            pass

        return items

    def qsize(self) -> int:
        return self.q.qsize()
