import threading
from typing import Callable, Dict, List, Any


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: Any) -> None:
        handlers = []
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        for h in handlers:
            try:
                h(payload)
            except Exception:
                pass
