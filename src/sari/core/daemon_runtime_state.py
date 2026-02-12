from __future__ import annotations

from collections.abc import Mapping
from threading import RLock

_LOCK = RLock()
_SNAPSHOT: dict[str, str] = {}


def publish_daemon_runtime_state(values: Mapping[str, object]) -> None:
    data = {str(k): str(v) for k, v in values.items()}
    with _LOCK:
        _SNAPSHOT.clear()
        _SNAPSHOT.update(data)


def get_daemon_runtime_state_snapshot() -> dict[str, str]:
    with _LOCK:
        return dict(_SNAPSHOT)


def clear_daemon_runtime_state() -> None:
    with _LOCK:
        _SNAPSHOT.clear()

