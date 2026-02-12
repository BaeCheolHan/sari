from types import SimpleNamespace

from sari.core.policy_engine import (
    load_daemon_policy,
    load_daemon_runtime_status,
    load_read_policy,
)
import sari.core.policy_engine as policy_engine


def test_load_read_policy_defaults():
    policy = load_read_policy(environ={})
    assert policy.gate_mode == "enforce"
    assert policy.max_range_lines == 200
    assert policy.max_preview_chars == 12000
    assert policy.max_snippet_results == 20
    assert policy.max_snippet_context_lines == 20
    assert policy.max_single_read_lines == 300


def test_load_read_policy_env_overrides():
    policy = load_read_policy(
        environ={
            "SARI_READ_GATE_MODE": "warn",
            "SARI_READ_MAX_RANGE_LINES": "123",
            "SARI_READ_MAX_PREVIEW_CHARS": "4567",
            "SARI_READ_MAX_SNIPPET_RESULTS": "7",
            "SARI_READ_MAX_SNIPPET_CONTEXT_LINES": "9",
            "SARI_READ_MAX_SINGLE_READ_LINES": "99",
        }
    )
    assert policy.gate_mode == "warn"
    assert policy.max_range_lines == 123
    assert policy.max_preview_chars == 4567
    assert policy.max_snippet_results == 7
    assert policy.max_snippet_context_lines == 9
    assert policy.max_single_read_lines == 99


def test_load_daemon_policy_reads_settings_and_env():
    settings_obj = SimpleNamespace(
        DAEMON_HEARTBEAT_SEC=5,
        DAEMON_IDLE_SEC=0,
        DAEMON_IDLE_WITH_ACTIVE=False,
        DAEMON_DRAIN_GRACE_SEC=0,
        get_int=lambda key, default: {
            "DAEMON_AUTOSTOP_GRACE_SEC": 60,
            "DAEMON_SHUTDOWN_INHIBIT_MAX_SEC": 20,
        }.get(key, default),
        get_bool=lambda key, default: {"DAEMON_AUTOSTOP": True}.get(key, default),
    )
    policy = load_daemon_policy(
        settings_obj=settings_obj,
        environ={"SARI_DAEMON_LEASE_TTL_SEC": "31"},
    )
    assert policy.heartbeat_sec == 5.0
    assert policy.autostop_enabled is True
    assert policy.autostop_grace_sec == 60
    assert policy.shutdown_inhibit_max_sec == 20
    assert policy.lease_ttl_sec == 31.0


def test_load_daemon_runtime_status_parses_marker_payload():
    status = load_daemon_runtime_status(
        {
            "SARI_DAEMON_SHUTDOWN_INTENT": "1",
            "SARI_DAEMON_SUICIDE_STATE": "grace",
            "SARI_DAEMON_ACTIVE_LEASES_COUNT": "3",
            "SARI_DAEMON_LEASES": '[{"id":"l1"}]',
            "SARI_DAEMON_LAST_REAP_AT": "10.5",
            "SARI_DAEMON_REAPER_LAST_RUN_AT": "10.6",
            "SARI_DAEMON_NO_CLIENT_SINCE": "9.0",
            "SARI_DAEMON_GRACE_REMAINING": "3.0",
            "SARI_DAEMON_GRACE_REMAINING_MS": "3000",
            "SARI_DAEMON_SHUTDOWN_ONCE_SET": "1",
            "SARI_DAEMON_LAST_EVENT_TS": "11.0",
            "SARI_DAEMON_EVENT_QUEUE_DEPTH": "2",
            "SARI_DAEMON_LAST_SHUTDOWN_REASON": "idle_timeout",
            "SARI_DAEMON_SHUTDOWN_REASON": "idle_timeout",
            "SARI_DAEMON_WORKERS_ALIVE": "[111,222]",
            "SARI_DAEMON_SIGNALS_DISABLED": "1",
        }
    )
    assert status.shutdown_intent is True
    assert status.signals_disabled is True
    assert status.suicide_state == "grace"
    assert status.active_leases_count == 3
    assert status.last_shutdown_reason == "idle_timeout"
    assert status.workers_alive == [111, 222]


def test_load_daemon_runtime_status_reuses_json_list_cache(monkeypatch):
    calls = {"n": 0}
    original = policy_engine.json.loads

    def _wrapped_loads(raw):
        calls["n"] += 1
        return original(raw)

    monkeypatch.setattr(policy_engine.json, "loads", _wrapped_loads)
    env = {
        "SARI_DAEMON_LEASES": '[{"id":"l1"}]',
        "SARI_DAEMON_WORKERS_ALIVE": "[111,222]",
    }

    load_daemon_runtime_status(env)
    load_daemon_runtime_status(env)

    # without cache this is 4 calls (2 keys x 2 invocations)
    assert calls["n"] <= 2
