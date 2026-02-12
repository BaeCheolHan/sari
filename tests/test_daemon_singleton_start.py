from types import SimpleNamespace
import sari.mcp.cli.daemon as d


def test_start_does_not_switch_to_free_port_when_target_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    monkeypatch.setattr("sari.mcp.cli.utils.is_port_in_use", lambda h, p: True)

    rc = d.check_port_availability(params)

    assert rc == 1
    assert params["port"] == 47779


def test_check_port_availability_tolerates_transient_busy(monkeypatch):
    params = {
        "host": "127.0.0.1",
        "port": 47779,
        "explicit_port": False,
        "registry": SimpleNamespace(find_free_port=lambda start_port: 47790),
    }

    states = iter([True, True, False])
    monkeypatch.setattr(
        "sari.mcp.cli.utils.is_port_in_use",
        lambda h, p: next(states),
    )
    monkeypatch.setattr("sari.mcp.cli.daemon.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sari.mcp.cli.daemon.smart_kill_port_owner", lambda h, p: False)

    rc = d.check_port_availability(params)
    assert rc is None
