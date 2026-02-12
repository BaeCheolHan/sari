import time
import threading
from collections import deque
from typing import Optional


class WarningSink:
    def __init__(self, max_recent: int = 50) -> None:
        self._max_recent = max(1, int(max_recent))
        self._recent = deque(maxlen=self._max_recent)
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def warn(
        self,
        reason_code: str,
        where: str,
        exc: Optional[BaseException] = None,
        extra: Optional[dict[str, object]] = None,
    ) -> None:
        code = str(reason_code or "UNKNOWN")
        where_text = str(where or "")
        with self._lock:
            self._counts[code] = int(self._counts.get(code, 0) or 0) + 1
            event = {
                "ts": float(time.time()),
                "reason_code": code,
                "where": where_text,
                "error": repr(exc) if exc is not None else "",
                "extra": dict(extra or {}),
            }
            self._recent.append(event)

    def warning_counts(self) -> dict[str, int]:
        with self._lock:
            return {str(k): int(v or 0) for k, v in self._counts.items()}

    def warnings_recent(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._recent]

    def count(self, reason_code: str) -> int:
        with self._lock:
            return int(self._counts.get(str(reason_code or ""), 0) or 0)

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()
            self._counts.clear()


warning_sink = WarningSink()


def warn(
    reason_code: str,
    where: str,
    exc: Optional[BaseException] = None,
    extra: Optional[dict[str, object]] = None,
) -> None:
    warning_sink.warn(reason_code=reason_code, where=where, exc=exc, extra=extra)
