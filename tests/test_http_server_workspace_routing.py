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


def test_search_returns_400_when_query_missing():
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.db = SimpleNamespace()
    handler.indexer = SimpleNamespace(cfg=SimpleNamespace(snippet_max_lines=3))
    handler.root_ids = []

    resp = Handler._handle_get(handler, "/search", {})
    assert resp["ok"] is False
    assert resp["status"] == 400
    assert "missing q" in resp["error"]


def test_search_uses_db_search_v2_and_returns_hits():
    class _Hit:
        def __init__(self, repo, path, score, snippet):
            self.repo = repo
            self.path = path
            self.score = score
            self.snippet = snippet

    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.root_ids = ["rid-1"]
    handler.indexer = SimpleNamespace(cfg=SimpleNamespace(snippet_max_lines=4))

    captured = {}

    def _search_v2(opts):
        captured["query"] = opts.query
        captured["limit"] = opts.limit
        captured["root_ids"] = opts.root_ids
        return ([_Hit("repo1", "a.py", 1.0, "x")], {"total": 1})

    handler.db = SimpleNamespace(search_v2=_search_v2, engine=None)

    resp = Handler._handle_get(handler, "/search", {"q": ["hello"], "limit": ["5"]})
    assert resp["ok"] is True
    assert resp["meta"]["total"] == 1
    assert resp["hits"][0]["path"] == "a.py"
    assert captured == {"query": "hello", "limit": 5, "root_ids": ["rid-1"]}
