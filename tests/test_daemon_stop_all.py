import sari.mcp.cli.daemon as d


def test_stop_without_endpoint_stops_all_registry_daemons(monkeypatch):
    killed = []
    monkeypatch.setattr(d, "list_registry_daemon_endpoints", lambda: [("127.0.0.1", 47779), ("127.0.0.1", 47790)])
    monkeypatch.setattr(d, "stop_one_endpoint", lambda h, p: killed.append((h, p)) or 0)

    rc = d.stop_daemon_process({"host": None, "port": None, "all": True})

    assert rc == 0
    assert killed == [("127.0.0.1", 47779), ("127.0.0.1", 47790)]


def test_stop_without_registry_falls_back_to_active_default_daemon(monkeypatch):
    killed = []
    monkeypatch.setattr(d, "list_registry_daemon_endpoints", lambda: [])
    monkeypatch.setattr(d, "_discover_daemon_endpoints_from_processes", lambda: [])
    monkeypatch.setattr(d, "get_daemon_address", lambda: ("127.0.0.1", 47779))
    monkeypatch.setattr(d, "is_daemon_running", lambda h, p: (h, p) == ("127.0.0.1", 47779))
    monkeypatch.setattr(d, "stop_one_endpoint", lambda h, p: killed.append((h, p)) or 0)

    rc = d.stop_daemon_process({"host": None, "port": None, "all": True})

    assert rc == 0
    assert killed == [("127.0.0.1", 47779)]


def test_stop_one_endpoint_uses_smart_kill_when_pid_missing(monkeypatch):
    monkeypatch.setattr(d, "is_daemon_running", lambda _h, _p: True)
    monkeypatch.setattr(d, "read_pid", lambda _h, _p: None)
    monkeypatch.setattr(d, "get_registry_targets", lambda _h, _p, _pid: (set(), set()))
    monkeypatch.setattr(d, "smart_kill_port_owner", lambda _h, _p: True)
    monkeypatch.setattr(d, "remove_pid", lambda: None)

    rc = d.stop_one_endpoint("127.0.0.1", 47779)
    assert rc == 0
