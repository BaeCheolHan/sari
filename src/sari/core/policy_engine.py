from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class ReadPolicy:
    gate_mode: str = "enforce"
    max_range_lines: int = 200
    max_reads_per_session: int = 25
    max_total_read_lines: int = 2500
    max_single_read_lines: int = 300
    max_preview_chars: int = 12000
    max_snippet_results: int = 20
    max_snippet_context_lines: int = 20


@dataclass(frozen=True)
class DaemonPolicy:
    heartbeat_sec: float = 5.0
    idle_sec: int = 0
    idle_with_active: bool = False
    drain_grace_sec: int = 0
    autostop_enabled: bool = True
    autostop_grace_sec: int = 60
    shutdown_inhibit_max_sec: int = 20
    lease_ttl_sec: float = 25.0


@dataclass(frozen=True)
class DaemonRuntimeStatus:
    signals_disabled: bool = False
    shutdown_intent: bool = False
    suicide_state: str = "idle"
    active_leases_count: int = 0
    leases: list[object] | None = None
    last_reap_at: float = 0.0
    reaper_last_run_at: float = 0.0
    no_client_since: float = 0.0
    grace_remaining: float = 0.0
    grace_remaining_ms: int = 0
    shutdown_once_set: bool = False
    last_event_ts: float = 0.0
    event_queue_depth: int = 0
    last_shutdown_reason: str = ""
    shutdown_reason: str = ""
    workers_alive: list[object] | None = None


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return environ if environ is not None else os.environ


def _to_int(value: object, default: int, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _to_float(value: object, default: float, *, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _to_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(default)


def _settings_attr(settings_obj: object, key: str, default: object) -> object:
    if settings_obj is None:
        return default
    try:
        return getattr(settings_obj, key)
    except Exception:
        return default


def _settings_int(settings_obj: object, key: str, default: int) -> int:
    if settings_obj is not None and hasattr(settings_obj, "get_int"):
        try:
            return int(settings_obj.get_int(key, default))
        except Exception:
            return int(default)
    return _to_int(_settings_attr(settings_obj, key, default), default)


def _settings_bool(settings_obj: object, key: str, default: bool) -> bool:
    if settings_obj is not None and hasattr(settings_obj, "get_bool"):
        try:
            return bool(settings_obj.get_bool(key, default))
        except Exception:
            return bool(default)
    return _to_bool(_settings_attr(settings_obj, key, default), default)


def load_read_policy(settings_obj: object | None = None, environ: Mapping[str, str] | None = None) -> ReadPolicy:
    env = _env(environ)
    gate_mode = str(env.get("SARI_READ_GATE_MODE", "enforce")).strip().lower()
    if gate_mode not in {"enforce", "warn"}:
        gate_mode = "enforce"
    return ReadPolicy(
        gate_mode=gate_mode,
        max_range_lines=_to_int(env.get("SARI_READ_MAX_RANGE_LINES", 200), 200, minimum=1),
        max_reads_per_session=_to_int(env.get("SARI_READ_MAX_READS_PER_SESSION", 25), 25, minimum=1),
        max_total_read_lines=_to_int(env.get("SARI_READ_MAX_TOTAL_LINES", 2500), 2500, minimum=1),
        max_single_read_lines=_to_int(env.get("SARI_READ_MAX_SINGLE_READ_LINES", 300), 300, minimum=1),
        max_preview_chars=_to_int(env.get("SARI_READ_MAX_PREVIEW_CHARS", 12000), 12000, minimum=100),
        max_snippet_results=_to_int(env.get("SARI_READ_MAX_SNIPPET_RESULTS", 20), 20, minimum=1),
        max_snippet_context_lines=_to_int(env.get("SARI_READ_MAX_SNIPPET_CONTEXT_LINES", 20), 20, minimum=0),
    )


def load_daemon_policy(settings_obj: object | None = None, environ: Mapping[str, str] | None = None) -> DaemonPolicy:
    env = _env(environ)
    heartbeat_default = _to_float(_settings_attr(settings_obj, "DAEMON_HEARTBEAT_SEC", 5), 5.0, minimum=0.05)
    return DaemonPolicy(
        heartbeat_sec=_to_float(env.get("SARI_DAEMON_HEARTBEAT_SEC", heartbeat_default), heartbeat_default, minimum=0.05),
        idle_sec=_to_int(env.get("SARI_DAEMON_IDLE_SEC", _settings_int(settings_obj, "DAEMON_IDLE_SEC", 0)), 0, minimum=0),
        idle_with_active=_to_bool(
            env.get("SARI_DAEMON_IDLE_WITH_ACTIVE", _settings_bool(settings_obj, "DAEMON_IDLE_WITH_ACTIVE", False)),
            False,
        ),
        drain_grace_sec=_to_int(
            env.get("SARI_DAEMON_DRAIN_GRACE_SEC", _settings_int(settings_obj, "DAEMON_DRAIN_GRACE_SEC", 0)),
            0,
            minimum=0,
        ),
        autostop_enabled=_to_bool(
            env.get("SARI_DAEMON_AUTOSTOP", _settings_bool(settings_obj, "DAEMON_AUTOSTOP", True)),
            True,
        ),
        autostop_grace_sec=_to_int(
            env.get("SARI_DAEMON_AUTOSTOP_GRACE_SEC", _settings_int(settings_obj, "DAEMON_AUTOSTOP_GRACE_SEC", 60)),
            60,
            minimum=1,
        ),
        shutdown_inhibit_max_sec=_to_int(
            env.get(
                "SARI_DAEMON_SHUTDOWN_INHIBIT_MAX_SEC",
                _settings_int(settings_obj, "DAEMON_SHUTDOWN_INHIBIT_MAX_SEC", 20),
            ),
            20,
            minimum=1,
        ),
        lease_ttl_sec=_to_float(
            env.get("SARI_DAEMON_LEASE_TTL_SEC", _settings_int(settings_obj, "DAEMON_LEASE_TTL_SEC", 25)),
            25.0,
            minimum=5.0,
        ),
    )


def _json_list(env: Mapping[str, str], key: str) -> list[object]:
    raw = str(env.get(key, "") or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def load_daemon_runtime_status(environ: Mapping[str, str] | None = None) -> DaemonRuntimeStatus:
    env = _env(environ)
    return DaemonRuntimeStatus(
        signals_disabled=_to_bool(env.get("SARI_DAEMON_SIGNALS_DISABLED"), False),
        shutdown_intent=_to_bool(env.get("SARI_DAEMON_SHUTDOWN_INTENT"), False),
        suicide_state=str(env.get("SARI_DAEMON_SUICIDE_STATE", "idle") or "idle"),
        active_leases_count=_to_int(env.get("SARI_DAEMON_ACTIVE_LEASES_COUNT", 0), 0, minimum=0),
        leases=_json_list(env, "SARI_DAEMON_LEASES"),
        last_reap_at=_to_float(env.get("SARI_DAEMON_LAST_REAP_AT", 0), 0.0, minimum=0.0),
        reaper_last_run_at=_to_float(env.get("SARI_DAEMON_REAPER_LAST_RUN_AT", 0), 0.0, minimum=0.0),
        no_client_since=_to_float(env.get("SARI_DAEMON_NO_CLIENT_SINCE", 0), 0.0, minimum=0.0),
        grace_remaining=_to_float(env.get("SARI_DAEMON_GRACE_REMAINING", 0), 0.0, minimum=0.0),
        grace_remaining_ms=_to_int(env.get("SARI_DAEMON_GRACE_REMAINING_MS", 0), 0, minimum=0),
        shutdown_once_set=_to_bool(env.get("SARI_DAEMON_SHUTDOWN_ONCE_SET"), False),
        last_event_ts=_to_float(env.get("SARI_DAEMON_LAST_EVENT_TS", 0), 0.0, minimum=0.0),
        event_queue_depth=_to_int(env.get("SARI_DAEMON_EVENT_QUEUE_DEPTH", 0), 0, minimum=0),
        last_shutdown_reason=str(env.get("SARI_DAEMON_LAST_SHUTDOWN_REASON", "") or ""),
        shutdown_reason=str(env.get("SARI_DAEMON_SHUTDOWN_REASON", "") or ""),
        workers_alive=_json_list(env, "SARI_DAEMON_WORKERS_ALIVE"),
    )
