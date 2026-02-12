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
        writer=SimpleNamespace(qsize=lambda: 7),
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
    monkeypatch.setattr(
        "sari.core.http_server.detect_orphan_daemons",
        lambda: [{"pid": 9999, "cmdline": "python -m sari.mcp.daemon"}],
    )

    resp = Handler._handle_get(handler, "/status", {"workspace_root": ["/tmp/target"]})
    assert resp["ok"] is True
    assert resp["workspace_root"] == "/tmp/target"
    assert resp["indexed_files"] == 2
    assert resp["repo_stats"] == {"r": 2}
    assert resp["orphan_daemon_count"] == 1
    assert len(resp["orphan_daemon_warnings"]) == 1
    assert resp["queue_depths"]["db_writer"] == 7
    assert "workspaces" in resp


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


def test_search_uses_db_search_and_returns_hits():
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

    def _search(opts):
        captured["query"] = opts.query
        captured["limit"] = opts.limit
        captured["root_ids"] = opts.root_ids
        return ([_Hit("repo1", "a.py", 1.0, "x")], {"total": 1})

    handler.db = SimpleNamespace(search=_search, engine=None)

    resp = Handler._handle_get(handler, "/search", {"q": ["hello"], "limit": ["5"]})
    assert resp["ok"] is True
    assert resp["meta"]["total"] == 1
    assert resp["hits"][0]["path"] == "a.py"
    assert captured == {"query": "hello", "limit": 5, "root_ids": ["rid-1"]}


def test_workspaces_endpoint_includes_per_workspace_file_count(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()

    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = str(ws_a)
    class _Rows:
        def fetchall(self):
            return [
                ("rid-a", 2, 1),
                ("rid-b", 0, 4),
            ]

    handler.db = SimpleNamespace(
        get_roots=lambda: [
            {"path": str(ws_a), "root_id": "rid-a", "file_count": 11, "updated_ts": 1700000000},
            {"path": str(ws_b), "root_id": "rid-b", "file_count": 3, "updated_ts": 1700000100},
        ],
        execute=lambda _sql: _Rows(),
    )
    handler.indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a), str(ws_b)]))
    handler.root_ids = []

    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.resolve_workspace_root",
        lambda: str(ws_a),
    )
    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.resolve_config_path",
        lambda _root: str(tmp_path / "cfg.json"),
    )
    monkeypatch.setattr(
        "sari.core.config.main.Config.load",
        lambda _path, workspace_root_override=None: SimpleNamespace(
            workspace_roots=[str(ws_a), str(ws_b)]
        ),
    )

    resp = Handler._handle_get(handler, "/workspaces", {})
    assert resp["ok"] is True
    assert resp["count"] == 2
    by_path = {w["path"]: w for w in resp["workspaces"]}
    assert by_path[str(ws_a)]["file_count"] == 11
    assert by_path[str(ws_b)]["file_count"] == 3
    assert by_path[str(ws_a)]["last_indexed_ts"] == 1700000000
    assert by_path[str(ws_b)]["last_indexed_ts"] == 1700000100
    assert by_path[str(ws_a)]["pending_count"] == 2
    assert by_path[str(ws_a)]["failed_count"] == 1
    assert by_path[str(ws_b)]["pending_count"] == 0
    assert by_path[str(ws_b)]["failed_count"] == 4
    assert by_path[str(ws_a)]["readable"] is True
    assert by_path[str(ws_a)]["watched"] is True


def test_sync_status_reports_db_metrics_failure(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [], db_path="/tmp/x.db")
    handler.indexer = SimpleNamespace(status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0))
    handler.root_ids = []

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})
    monkeypatch.setattr("sari.core.http_server.os.path.exists", lambda _p: (_ for _ in ()).throw(PermissionError("denied")))

    resp = Handler._handle_get(handler, "/status", {})
    assert resp["system_metrics"]["db_metrics_ok"] is False
    assert resp["status_warning_counts"]["DB_STORAGE_METRICS_FAILED"] >= 1


def test_sync_workspaces_tracks_normalize_fallback(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = str(ws_a)
    handler.db = SimpleNamespace(get_roots=lambda: [{"path": str(ws_a)}], execute=lambda _sql: SimpleNamespace(fetchall=lambda: []))
    handler.indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a)]))
    handler.root_ids = []

    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.resolve_workspace_root", lambda: str(ws_a))
    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.resolve_config_path", lambda _root: str(tmp_path / "cfg.json"))
    monkeypatch.setattr("sari.core.config.main.Config.load", lambda _path, workspace_root_override=None: SimpleNamespace(workspace_roots=[str(ws_a)]))
    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.normalize_path", lambda _p: (_ for _ in ()).throw(RuntimeError("normalize failed")))

    resp = Handler._handle_get(handler, "/workspaces", {})
    assert resp["normalization"]["fallback_count"] >= 1
    assert resp["workspaces"][0]["normalized_by"] == "fallback"


def test_sync_status_flags_registry_resolve_failure(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = True
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0))
    handler.root_ids = []

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})
    monkeypatch.setattr(
        "sari.mcp.workspace_registry.Registry.get_instance",
        lambda: SimpleNamespace(get_or_create=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("registry fail"))),
    )

    resp = Handler._handle_get(handler, "/status", {"workspace_root": ["/tmp/target"]})
    assert resp["registry_resolve_failed"] is True
    assert resp["status_warning_counts"]["REGISTRY_RESOLVE_FAILED"] >= 1


def test_sync_status_rejects_unregistered_workspace_query_in_shared_gateway(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = True
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0),
        cfg=SimpleNamespace(workspace_roots=["/tmp/default"]),
    )
    handler.root_ids = []

    called = {}

    def _get_or_create(ws, persistent=True, track_ref=False):
        called["workspace"] = ws
        return SimpleNamespace(db=handler.db, indexer=handler.indexer)

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})
    monkeypatch.setattr(
        "sari.mcp.workspace_registry.Registry.get_instance",
        lambda: SimpleNamespace(get_or_create=_get_or_create),
    )

    resp = Handler._handle_get(handler, "/status", {"workspace_root": ["/tmp/not-registered"]})
    assert resp["workspace_root"] == "/tmp/default"
    assert called["workspace"] == "/tmp/default"
    assert resp["status_warning_counts"]["WORKSPACE_NOT_REGISTERED"] >= 1


def test_sync_status_warns_when_workspace_query_parse_fails(monkeypatch):
    class _BadQs:
        def get(self, _key):
            raise RuntimeError("bad querystring")

    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0))
    handler.root_ids = []
    handler.headers = {"X-Request-ID": "req-123"}

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})

    resp = Handler._handle_get(handler, "/status", _BadQs())
    assert resp["status_warning_counts"]["WORKSPACE_QUERY_PARSE_FAILED"] >= 1


def test_sync_status_includes_shutdown_runtime_markers(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0))
    handler.root_ids = []

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})
    monkeypatch.setenv("SARI_DAEMON_SHUTDOWN_INTENT", "1")
    monkeypatch.setenv("SARI_DAEMON_ACTIVE_LEASES_COUNT", "3")
    monkeypatch.setenv("SARI_DAEMON_LAST_REAP_AT", "77.1")
    monkeypatch.setenv("SARI_DAEMON_LAST_SHUTDOWN_REASON", "idle_timeout")
    monkeypatch.setenv("SARI_DAEMON_SUICIDE_STATE", "grace")
    monkeypatch.setenv("SARI_DAEMON_REAPER_LAST_RUN_AT", "77.1")
    monkeypatch.setenv("SARI_DAEMON_NO_CLIENT_SINCE", "70.0")
    monkeypatch.setenv("SARI_DAEMON_GRACE_REMAINING", "2.0")
    monkeypatch.setenv("SARI_DAEMON_GRACE_REMAINING_MS", "2000")
    monkeypatch.setenv("SARI_DAEMON_SHUTDOWN_ONCE_SET", "1")
    monkeypatch.setenv("SARI_DAEMON_LAST_EVENT_TS", "78.0")
    monkeypatch.setenv("SARI_DAEMON_EVENT_QUEUE_DEPTH", "2")
    monkeypatch.setenv("SARI_DAEMON_LEASES", '[{"id":"l1"}]')
    monkeypatch.setenv("SARI_DAEMON_WORKERS_ALIVE", "[999]")

    resp = Handler._handle_get(handler, "/status", {})
    assert resp["shutdown_intent"] is True
    assert resp["active_leases_count"] == 3
    assert resp["last_reap_at"] == 77.1
    assert resp["last_shutdown_reason"] == "idle_timeout"
    assert resp["suicide_state"] == "grace"
    assert resp["reaper_last_run_at"] == 77.1
    assert resp["no_client_since"] == 70.0
    assert resp["grace_remaining"] == 2.0
    assert resp["grace_remaining_ms"] == 2000
    assert resp["shutdown_once_set"] is True
    assert resp["last_event_ts"] == 78.0
    assert resp["event_queue_depth"] == 2
    assert isinstance(resp["leases"], list)
    assert isinstance(resp["workers_alive"], list)


def test_sync_status_contract_snapshot(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0))
    handler.root_ids = []

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})
    monkeypatch.setenv("SARI_DAEMON_SUICIDE_STATE", "idle")

    resp = Handler._handle_get(handler, "/status", {})
    required = {
        "suicide_state",
        "active_leases_count",
        "no_client_since",
        "grace_remaining_ms",
        "shutdown_reason",
        "event_queue_depth",
        "last_event_ts",
        "workers_alive",
        "warnings_recent",
        "warning_counts",
    }
    missing = required - set(resp.keys())
    assert not missing, f"missing status contract fields: {sorted(missing)}"


def test_sync_status_uses_indexer_runtime_status_overlay(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.server_host = "127.0.0.1"
    handler.server_port = 47777
    handler.server_version = "0.6.11"
    handler.start_time = 0
    handler.db = SimpleNamespace(get_repo_stats=lambda root_ids=None: {}, get_roots=lambda: [])
    handler.indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, scan_finished_ts=1, indexed_files=1, scanned_files=1, errors=0),
        get_runtime_status=lambda: {
            "index_ready": False,
            "scan_finished_ts": 0,
            "scanned_files": 33,
            "indexed_files": 21,
            "symbols_extracted": 55,
            "errors": 2,
            "status_source": "worker_progress",
        },
    )
    handler.root_ids = []

    monkeypatch.setattr("sari.core.http_server.detect_orphan_daemons", lambda: [])
    monkeypatch.setattr("sari.core.http_server.get_system_metrics", lambda: {"process_cpu_percent": 0, "memory_percent": 0})

    resp = Handler._handle_get(handler, "/status", {})
    assert resp["index_ready"] is False
    assert resp["scanned_files"] == 33
    assert resp["indexed_files"] == 21
    assert resp["errors"] == 2
    assert resp["status_source"] == "worker_progress"


def test_sync_errors_endpoint_returns_log_and_warning_payload(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.db = SimpleNamespace()
    handler.indexer = SimpleNamespace()
    handler.root_ids = []
    handler._init_request_status()

    monkeypatch.setattr(
        handler,
        "_read_recent_log_error_entries",
        lambda limit=50: [{"text": "line1", "ts": 0.0}, {"text": "line2", "ts": 0.0}],
    )
    monkeypatch.setattr("sari.core.http_server.warning_sink.warnings_recent", lambda: [{"reason_code": "E1"}])
    monkeypatch.setattr("sari.core.http_server.warning_sink.warning_counts", lambda: {"E1": 1})

    resp = Handler._handle_get(handler, "/errors", {"limit": ["2"]})
    assert resp["ok"] is True
    assert resp["limit"] == 2
    assert resp["log_errors"] == ["line1", "line2"]
    assert isinstance(resp["warnings_recent"], list)


def test_sync_errors_endpoint_applies_filters(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.shared_http_gateway = False
    handler.workspace_root = "/tmp/default"
    handler.db = SimpleNamespace()
    handler.indexer = SimpleNamespace()
    handler.root_ids = []
    handler._init_request_status()

    now = 1_700_000_000.0
    monkeypatch.setattr("sari.core.http_server.time.time", lambda: now)
    monkeypatch.setattr(
        handler,
        "_read_recent_log_error_entries",
        lambda limit=50: [
            {"text": "old-log", "ts": now - 1000},
            {"text": "new-log", "ts": now - 10},
        ],
    )
    monkeypatch.setattr(
        "sari.core.http_server.warning_sink.warnings_recent",
        lambda: [
            {"reason_code": "A", "ts": now - 1000},
            {"reason_code": "B", "ts": now - 5},
        ],
    )
    monkeypatch.setattr("sari.core.http_server.warning_sink.warning_counts", lambda: {"A": 1, "B": 1})

    resp = Handler._handle_get(
        handler,
        "/errors",
        {"source": ["warning"], "reason_code": ["B"], "since_sec": ["60"], "limit": ["10"]},
    )
    assert resp["source"] == "warning"
    assert resp["log_errors"] == []
    assert len(resp["warnings_recent"]) == 1
    assert resp["warnings_recent"][0]["reason_code"] == "B"
