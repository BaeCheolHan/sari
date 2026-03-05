"""데몬 런타임 health 판정 공통 로직."""

from __future__ import annotations


def evaluate_daemon_health(
    *,
    pid_alive: bool,
    heartbeat_age_sec: float,
    stale_timeout_sec: float,
    lease_valid: bool,
    registry_degraded: bool,
    status_reason_detail: dict[str, object] | None = None,
) -> dict[str, object]:
    """데몬 상태를 다중 신호(pid/heartbeat/lease/registry)로 판정한다."""
    if not pid_alive:
        health_state = "dead"
        status_reason = "process_dead"
    elif heartbeat_age_sec < 0:
        health_state = "degraded"
        status_reason = "heartbeat_parse_error"
    elif heartbeat_age_sec > float(stale_timeout_sec):
        health_state = "stale"
        status_reason = "heartbeat_stale_but_pid_alive"
    elif not lease_valid:
        health_state = "degraded"
        status_reason = "lease_invalid_but_pid_alive"
    elif registry_degraded:
        health_state = "degraded"
        status_reason = "registry_degraded_but_pid_alive"
    else:
        health_state = "running"
        status_reason = "running"
    detail = status_reason_detail or {}
    return {
        "health_state": health_state,
        "status_reason": status_reason,
        "pid_alive": bool(pid_alive),
        "lease_valid": bool(lease_valid),
        "health_signals": {
            "pid_alive": bool(pid_alive),
            "heartbeat_age_sec": float(heartbeat_age_sec),
            "stale_timeout_sec": float(stale_timeout_sec),
            "lease_valid": bool(lease_valid),
            "registry_degraded": bool(registry_degraded),
        },
        "status_reason_detail": {
            "deployment_state": detail.get("deployment_state"),
            "health_fail_streak": detail.get("health_fail_streak"),
            "last_health_error": detail.get("last_health_error"),
        },
    }
