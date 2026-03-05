from __future__ import annotations

from sari.services.daemon.health import evaluate_daemon_health


def test_evaluate_daemon_health_prioritizes_dead_state() -> None:
    payload = evaluate_daemon_health(
        pid_alive=False,
        heartbeat_age_sec=1.0,
        stale_timeout_sec=15.0,
        lease_valid=True,
        registry_degraded=False,
    )
    assert payload["health_state"] == "dead"
    assert payload["status_reason"] == "process_dead"


def test_evaluate_daemon_health_uses_lease_and_registry_signals() -> None:
    lease_payload = evaluate_daemon_health(
        pid_alive=True,
        heartbeat_age_sec=1.0,
        stale_timeout_sec=15.0,
        lease_valid=False,
        registry_degraded=False,
    )
    assert lease_payload["health_state"] == "degraded"
    assert lease_payload["status_reason"] == "lease_invalid_but_pid_alive"

    registry_payload = evaluate_daemon_health(
        pid_alive=True,
        heartbeat_age_sec=1.0,
        stale_timeout_sec=15.0,
        lease_valid=True,
        registry_degraded=True,
    )
    assert registry_payload["health_state"] == "degraded"
    assert registry_payload["status_reason"] == "registry_degraded_but_pid_alive"
