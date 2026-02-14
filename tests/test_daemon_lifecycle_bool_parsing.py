from types import SimpleNamespace

from sari.mcp.cli.daemon_lifecycle import (
    extract_daemon_start_params,
    extract_daemon_stop_params,
    needs_upgrade_or_drain,
)


def test_extract_daemon_start_params_string_false_port_is_not_explicit():
    args = SimpleNamespace(daemon_host="", daemon_port="false")
    params = extract_daemon_start_params(
        args,
        workspace_root_resolver=lambda: "/tmp/ws",
        registry_factory=lambda: SimpleNamespace(resolve_workspace_daemon=lambda _ws: None),
        daemon_address_resolver=lambda: ("127.0.0.1", 47779),
        default_host="127.0.0.1",
        default_port=47779,
    )
    assert params["explicit_port"] is False


def test_extract_daemon_stop_params_string_false_all_does_not_enable_all_mode():
    args = SimpleNamespace(all="false", daemon_host="127.0.0.1", daemon_port=47779)
    params = extract_daemon_stop_params(args, default_host="127.0.0.1", default_port=47779)
    assert params["all"] is False
    assert params["host"] == "127.0.0.1"
    assert params["port"] == 47779


def test_needs_upgrade_or_drain_string_false_draining_does_not_force_restart():
    identify = {"version": "1.0.0", "draining": "false"}
    assert needs_upgrade_or_drain(identify, local_version="1.0.0") is False
