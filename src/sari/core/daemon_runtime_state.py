from __future__ import annotations

import os
from collections.abc import Mapping
from threading import RLock

_LOCK = RLock()
_SNAPSHOT: dict[str, str] = {}

RUNTIME_SIGNALS_DISABLED = "SARI_DAEMON_SIGNALS_DISABLED"
RUNTIME_SHUTDOWN_INTENT = "SARI_DAEMON_SHUTDOWN_INTENT"
RUNTIME_SUICIDE_STATE = "SARI_DAEMON_SUICIDE_STATE"
RUNTIME_ACTIVE_LEASES_COUNT = "SARI_DAEMON_ACTIVE_LEASES_COUNT"
RUNTIME_LEASES = "SARI_DAEMON_LEASES"
RUNTIME_LAST_REAP_AT = "SARI_DAEMON_LAST_REAP_AT"
RUNTIME_REAPER_LAST_RUN_AT = "SARI_DAEMON_REAPER_LAST_RUN_AT"
RUNTIME_NO_CLIENT_SINCE = "SARI_DAEMON_NO_CLIENT_SINCE"
RUNTIME_GRACE_REMAINING = "SARI_DAEMON_GRACE_REMAINING"
RUNTIME_GRACE_REMAINING_MS = "SARI_DAEMON_GRACE_REMAINING_MS"
RUNTIME_SHUTDOWN_ONCE_SET = "SARI_DAEMON_SHUTDOWN_ONCE_SET"
RUNTIME_LAST_EVENT_TS = "SARI_DAEMON_LAST_EVENT_TS"
RUNTIME_EVENT_QUEUE_DEPTH = "SARI_DAEMON_EVENT_QUEUE_DEPTH"
RUNTIME_LAST_SHUTDOWN_REASON = "SARI_DAEMON_LAST_SHUTDOWN_REASON"
RUNTIME_SHUTDOWN_REASON = "SARI_DAEMON_SHUTDOWN_REASON"
RUNTIME_WORKERS_ALIVE = "SARI_DAEMON_WORKERS_ALIVE"
RUNTIME_HOST = "SARI_DAEMON_HOST"
RUNTIME_PORT = "SARI_DAEMON_PORT"
RUNTIME_RSS_BYTES = "SARI_DAEMON_RSS_BYTES"
RUNTIME_RSS_MB = "SARI_DAEMON_RSS_MB"


def _bool_flag(raw: object, default: bool = True) -> bool:
    if raw is None:
        return bool(default)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(default)


def _mirror_to_env(data: Mapping[str, str], mirror_env: bool | None) -> None:
    should_mirror = (
        _bool_flag(os.environ.get("SARI_DAEMON_RUNTIME_ENV_MIRROR", "1"), True)
        if mirror_env is None
        else bool(mirror_env)
    )
    if should_mirror:
        for key, value in data.items():
            os.environ[key] = value


def publish_daemon_runtime_state(
    values: Mapping[str, object], *, mirror_env: bool | None = None
) -> None:
    data = {str(k): str(v) for k, v in values.items()}
    with _LOCK:
        _SNAPSHOT.clear()
        _SNAPSHOT.update(data)
    _mirror_to_env(data, mirror_env)


def update_daemon_runtime_state(
    values: Mapping[str, object], *, mirror_env: bool | None = None
) -> None:
    data = {str(k): str(v) for k, v in values.items()}
    with _LOCK:
        _SNAPSHOT.update(data)
    _mirror_to_env(data, mirror_env)


def get_daemon_runtime_state_snapshot() -> dict[str, str]:
    with _LOCK:
        return dict(_SNAPSHOT)


def clear_daemon_runtime_state() -> None:
    with _LOCK:
        _SNAPSHOT.clear()
