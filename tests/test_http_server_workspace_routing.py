from types import SimpleNamespace

from sari.core.http_server import Handler


def test_status_routes_to_selected_workspace(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = True
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace()
    handler.indexer = SimpleNamespace()
    handler.root_ids = []

    selected_db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {"r": 2},
        get_roots=lambda: [{"path": "/tmp/target", "root_id": "/tmp/target"}],
        db_path="/tmp/index.db",
    )
    selected_indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, scan_finished_ts=123, indexed_files=2, scanned_files=2, errors=0)
    )
    selected_state = SimpleNamespace(db=selected_db, indexer=selected_indexer)

    monkeypatch.setattr(
        "sari.mcp.workspace_registry.Registry.get_instance",
        lambda: SimpleNamespace(get_or_create=lambda ws, persistent=True, track_ref=False: selected_state),
    )
    monkeypatch.setattr(
        "sari.core.http_server.get_system_metrics",
        lambda: {"uptime": 1, "db_size": 0, "process_cpu_percent": 0, "memory_percent": 0},
    )

    resp = Handler._handle_get(handler, "/status", {"workspace_root": ["/tmp/target"]})
    assert resp["ok"] is True
    assert resp["workspace_root"] == "/tmp/target"
    assert resp["indexed_files"] == 2
    assert resp["repo_stats"] == {"r": 2}

