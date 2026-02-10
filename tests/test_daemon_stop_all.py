import sari.mcp.cli.daemon as d


def test_stop_without_endpoint_stops_all_registry_daemons(monkeypatch):
    killed = []
    monkeypatch.setattr(d, "list_registry_daemon_endpoints", lambda: [("127.0.0.1", 47779), ("127.0.0.1", 47790)])
    monkeypatch.setattr(d, "stop_one_endpoint", lambda h, p: killed.append((h, p)) or 0)

    rc = d.stop_daemon_process({"host": None, "port": None, "all": True})

    assert rc == 0
    assert killed == [("127.0.0.1", 47779), ("127.0.0.1", 47790)]
