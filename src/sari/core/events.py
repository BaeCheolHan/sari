import threading
from collections.abc import Callable

class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[Callable[[object], None]]] = {}

    def subscribe(self, topic: str, handler: Callable[[object], None]) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: object) -> None:
        handlers = []
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        for h in handlers:
            try:
                h(payload)
            except Exception:
                pass
