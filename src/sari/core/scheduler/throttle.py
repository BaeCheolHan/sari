import time
import threading
from typing import Callable, Optional

class TokenBucket:
    """Rate limiting using Token Bucket algorithm."""
    def __init__(self, capacity: float, fill_rate: float):
        self.capacity = capacity
        self.tokens = capacity
        self.fill_rate = fill_rate
        self.last_update = time.time()
        self._lock = threading.Lock()

    def consume(self, amount: float = 1.0) -> bool:
        with self._lock:
            now = time.time()
            # Refill
            self.tokens = min(self.capacity, self.tokens + (now - self.last_update) * self.fill_rate)
            self.last_update = now
            
            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False

class AdaptiveDebouncer:
    """
    Debounces events with an adaptive window.
    Window increases when event burst is detected.
    """
    def __init__(self, callback: Callable, min_delay: float = 0.5, max_delay: float = 5.0):
        self.callback = callback
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.current_delay = min_delay
        self._timer: Optional[threading.Timer] = None
        self._last_event_time = 0.0
        self._event_count_in_window = 0
        self._lock = threading.Lock()

    def handle_event(self, *args, **kwargs):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_event_time
            self._last_event_time = now
            
            # If events come too fast, increase delay
            if elapsed < self.min_delay:
                self.current_delay = min(self.max_delay, self.current_delay * 1.5)
            else:
                self.current_delay = max(self.min_delay, self.current_delay * 0.8)

            if self._timer:
                self._timer.cancel()
            
            self._timer = threading.Timer(self.current_delay, self.callback, args=args, kwargs=kwargs)
            self._timer.start()
