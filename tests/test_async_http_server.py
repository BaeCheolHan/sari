import json
import pytest
from types import SimpleNamespace
from starlette.datastructures import QueryParams

from sari.core.async_http_server import AsyncHttpServer, serve_async


@pytest.mark.asyncio
async def test_async_http_server_search_tolerates_dict_hits():
    db = SimpleNamespace(
        search=lambda _opts: ([{"path": "a.py", "score": 1.0}], {"total": 1}),
        engine=None,
    )
    indexer = SimpleNamespace(cfg=SimpleNamespace(snippet_max_lines=3))
    server = AsyncHttpServer(db, indexer, root_ids=["rid"])
    req = SimpleNamespace(query_params=QueryParams("q=test&limit=5"))

    resp = await server.search(req)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_async_http_server_workspaces_includes_file_counts(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir()
    ws_b.mkdir()

    class _Rows:
        def fetchall(self):
            return [
                ("rid-a", 1, 0),
                ("rid-b", 3, 2),
            ]

    db = SimpleNamespace(
        get_roots=lambda: [
            {"path": str(ws_a), "root_id": "rid-a", "file_count": 5, "updated_ts": 1700000200},
            {"path": str(ws_b), "root_id": "rid-b", "file_count": 2, "updated_ts": 1700000300},
        ],
        execute=lambda _sql: _Rows(),
    )
    indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a), str(ws_b)]))
    server = AsyncHttpServer(db, indexer, workspace_root=str(ws_a))

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

    resp = await server.workspaces(SimpleNamespace())
    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))
    assert data["ok"] is True
    by_path = {w["path"]: w for w in data["workspaces"]}
    assert by_path[str(ws_a)]["file_count"] == 5
    assert by_path[str(ws_b)]["file_count"] == 2
    assert by_path[str(ws_a)]["pending_count"] == 1
    assert by_path[str(ws_a)]["failed_count"] == 0
    assert by_path[str(ws_b)]["pending_count"] == 3
    assert by_path[str(ws_b)]["failed_count"] == 2


@pytest.mark.asyncio
async def test_async_http_server_dashboard_uses_new_html():
    db = SimpleNamespace()
    indexer = SimpleNamespace(cfg=SimpleNamespace(snippet_max_lines=3))
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")

    resp = await server.dashboard(SimpleNamespace())
    assert resp.status_code == 200
    body = resp.body.decode("utf-8")
    assert "SARI Insight" in body
    assert "Workspaces" in body
    assert "Retry Queue" in body
    assert "Permanent Failures" in body
    assert "title={titleText}" in body
    assert "const rawDetail = (r.error ?? r.detail ?? \"\")" in body


@pytest.mark.asyncio
async def test_async_http_server_status_includes_orphan_daemon_warning(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    monkeypatch.setattr(
        "sari.core.async_http_server.detect_orphan_daemons",
        lambda: [{"pid": 2222, "cmdline": "python -m sari.mcp.daemon"}],
    )

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["orphan_daemon_count"] == 1
    assert len(data["orphan_daemon_warnings"]) == 1


@pytest.mark.asyncio
async def test_async_http_server_status_marks_metrics_failure(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    monkeypatch.setattr("sari.core.utils.system.get_system_metrics", lambda: (_ for _ in ()).throw(RuntimeError("fail")))

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["system_metrics"]["metrics_ok"] is False
    assert data["status_warning_counts"]["SYSTEM_METRICS_FAILED"] >= 1
    assert data["warning_counts"]["SYSTEM_METRICS_FAILED"] >= 1
    assert any(w.get("reason_code") == "SYSTEM_METRICS_FAILED" for w in data["warnings_recent"])


@pytest.mark.asyncio
async def test_async_http_server_status_marks_db_metrics_failure(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
        db_path="/tmp/x.db",
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    monkeypatch.setattr("sari.core.async_http_server.os.path.exists", lambda _p: (_ for _ in ()).throw(PermissionError("denied")))

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["system_metrics"]["db_metrics_ok"] is False
    assert data["status_warning_counts"]["DB_STORAGE_METRICS_FAILED"] >= 1


@pytest.mark.asyncio
async def test_async_http_server_status_includes_signals_disabled_flag(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    monkeypatch.setenv("SARI_DAEMON_SIGNALS_DISABLED", "1")
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["signals_disabled"] is True


@pytest.mark.asyncio
async def test_async_http_server_status_includes_shutdown_runtime_markers(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    monkeypatch.setenv("SARI_DAEMON_SHUTDOWN_INTENT", "1")
    monkeypatch.setenv("SARI_DAEMON_ACTIVE_LEASES_COUNT", "2")
    monkeypatch.setenv("SARI_DAEMON_LAST_REAP_AT", "123.5")
    monkeypatch.setenv("SARI_DAEMON_LAST_SHUTDOWN_REASON", "autostop_no_clients")
    monkeypatch.setenv("SARI_DAEMON_SUICIDE_STATE", "grace")
    monkeypatch.setenv("SARI_DAEMON_REAPER_LAST_RUN_AT", "123.5")
    monkeypatch.setenv("SARI_DAEMON_NO_CLIENT_SINCE", "120.0")
    monkeypatch.setenv("SARI_DAEMON_GRACE_REMAINING", "3.0")
    monkeypatch.setenv("SARI_DAEMON_GRACE_REMAINING_MS", "3000")
    monkeypatch.setenv("SARI_DAEMON_SHUTDOWN_ONCE_SET", "1")
    monkeypatch.setenv("SARI_DAEMON_LAST_EVENT_TS", "124.0")
    monkeypatch.setenv("SARI_DAEMON_EVENT_QUEUE_DEPTH", "4")
    monkeypatch.setenv("SARI_DAEMON_LEASES", '[{"id":"l1"}]')
    monkeypatch.setenv("SARI_DAEMON_WORKERS_ALIVE", "[321,654]")
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["shutdown_intent"] is True
    assert data["active_leases_count"] == 2
    assert data["last_reap_at"] == 123.5
    assert data["last_shutdown_reason"] == "autostop_no_clients"
    assert data["suicide_state"] == "grace"
    assert data["reaper_last_run_at"] == 123.5
    assert data["no_client_since"] == 120.0
    assert data["grace_remaining"] == 3.0
    assert data["grace_remaining_ms"] == 3000
    assert data["shutdown_once_set"] is True
    assert data["last_event_ts"] == 124.0
    assert data["event_queue_depth"] == 4
    assert isinstance(data["leases"], list)
    assert isinstance(data["workers_alive"], list)


@pytest.mark.asyncio
async def test_async_http_server_status_contract_snapshot(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    monkeypatch.setenv("SARI_DAEMON_SUICIDE_STATE", "idle")
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
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
    missing = required - set(data.keys())
    assert not missing, f"missing status contract fields: {sorted(missing)}"


@pytest.mark.asyncio
async def test_async_http_server_status_uses_indexer_runtime_status_overlay(monkeypatch):
    db = SimpleNamespace(
        get_repo_stats=lambda root_ids=None: {},
        get_roots=lambda: [],
        fts_enabled=True,
    )
    indexer = SimpleNamespace(
        status=SimpleNamespace(index_ready=True, last_scan_ts=1, scanned_files=2, indexed_files=2, errors=0),
        get_runtime_status=lambda: {
            "index_ready": False,
            "scan_finished_ts": 0,
            "scanned_files": 44,
            "indexed_files": 22,
            "symbols_extracted": 66,
            "errors": 3,
            "status_source": "worker_progress",
        },
        get_last_commit_ts=lambda: 0,
        get_performance_metrics=lambda: {},
        get_queue_depths=lambda: {},
    )
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    monkeypatch.setattr("sari.core.async_http_server.detect_orphan_daemons", lambda: [])

    resp = await server.status(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["index_ready"] is False
    assert data["scanned_files"] == 44
    assert data["indexed_files"] == 22
    assert data["errors"] == 3
    assert data["status_source"] == "worker_progress"


@pytest.mark.asyncio
async def test_async_http_server_errors_endpoint_returns_payload(monkeypatch):
    db = SimpleNamespace()
    indexer = SimpleNamespace(status=SimpleNamespace())
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    monkeypatch.setattr(server, "_read_recent_log_errors", lambda limit=50: ["err-a"])
    monkeypatch.setattr("sari.core.async_http_server.warning_sink.warnings_recent", lambda: [{"reason_code": "X"}])
    monkeypatch.setattr("sari.core.async_http_server.warning_sink.warning_counts", lambda: {"X": 1})

    req = SimpleNamespace(query_params=QueryParams("limit=1"))
    resp = await server.errors(req)
    data = json.loads(resp.body.decode("utf-8"))
    assert data["ok"] is True
    assert data["limit"] == 1
    assert data["log_errors"] == ["err-a"]
    assert data["warning_counts"]["X"] == 1


@pytest.mark.asyncio
async def test_async_http_server_errors_endpoint_applies_filters(monkeypatch):
    db = SimpleNamespace()
    indexer = SimpleNamespace(status=SimpleNamespace())
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws")
    now = 1_700_000_000.0
    monkeypatch.setattr("sari.core.async_http_server.time.time", lambda: now)
    monkeypatch.setattr(
        server,
        "_read_recent_log_error_entries",
        lambda limit=50: [
            {"text": "old-log", "ts": now - 1000},
            {"text": "new-log", "ts": now - 1},
        ],
    )
    monkeypatch.setattr(
        "sari.core.async_http_server.warning_sink.warnings_recent",
        lambda: [
            {"reason_code": "A", "ts": now - 1000},
            {"reason_code": "B", "ts": now - 1},
        ],
    )
    monkeypatch.setattr("sari.core.async_http_server.warning_sink.warning_counts", lambda: {"A": 1, "B": 1})

    req = SimpleNamespace(query_params=QueryParams("source=log&since_sec=60&limit=10"))
    resp = await server.errors(req)
    data = json.loads(resp.body.decode("utf-8"))
    assert data["source"] == "log"
    assert data["warnings_recent"] == []
    assert data["log_errors"] == ["new-log"]


@pytest.mark.asyncio
async def test_async_http_server_workspaces_tracks_normalize_fallback(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()

    db = SimpleNamespace(
        get_roots=lambda: [{"path": str(ws_a), "root_id": "rid-a", "file_count": 1, "updated_ts": 1700000200}],
        execute=lambda _sql: SimpleNamespace(fetchall=lambda: []),
    )
    indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a)]))
    server = AsyncHttpServer(db, indexer, workspace_root=str(ws_a))

    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.resolve_config_path",
        lambda _root: str(tmp_path / "cfg.json"),
    )
    monkeypatch.setattr(
        "sari.core.config.main.Config.load",
        lambda _path, workspace_root_override=None: SimpleNamespace(workspace_roots=[str(ws_a)]),
    )
    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.normalize_path",
        lambda _p: (_ for _ in ()).throw(RuntimeError("normalize failed")),
    )

    resp = await server.workspaces(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["normalization"]["fallback_count"] >= 1
    assert data["status_warning_counts"]["WORKSPACE_NORMALIZE_FAILED"] >= 1
    assert data["workspaces"][0]["normalized_by"] == "fallback"


@pytest.mark.asyncio
async def test_async_http_server_workspaces_reports_root_row_parse_errors(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()

    class _BadPath:
        def __str__(self):
            raise RuntimeError("bad path")

    db = SimpleNamespace(
        get_roots=lambda: [{"path": _BadPath(), "root_id": "rid-a", "file_count": 1, "updated_ts": 1700000200}],
        execute=lambda _sql: SimpleNamespace(fetchall=lambda: []),
    )
    indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a)]))
    server = AsyncHttpServer(db, indexer, workspace_root=str(ws_a))

    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.resolve_config_path",
        lambda _root: str(tmp_path / "cfg.json"),
    )
    monkeypatch.setattr(
        "sari.core.config.main.Config.load",
        lambda _path, workspace_root_override=None: SimpleNamespace(workspace_roots=[str(ws_a)]),
    )

    resp = await server.workspaces(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["row_parse_error_count"] >= 1
    assert data["status_warning_counts"]["WORKSPACE_ROW_PARSE_FAILED"] >= 1


@pytest.mark.asyncio
async def test_async_http_server_lifespan_best_effort_shutdown():
    closed = {"close": 0, "aclose": 0}

    class _Closable:
        def close(self):
            closed["close"] += 1

    class _AClosable:
        async def aclose(self):
            closed["aclose"] += 1

    db = _Closable()
    indexer = _AClosable()
    server = AsyncHttpServer(db, indexer, workspace_root="/tmp/ws", mcp_server=_Closable())
    app = SimpleNamespace(state=SimpleNamespace())

    async with server.lifespan(app):
        assert app.state.db is db

    assert closed["close"] >= 2
    assert closed["aclose"] >= 1


@pytest.mark.asyncio
async def test_async_http_server_workspaces_warns_on_failed_tasks_aggregate_error(monkeypatch, tmp_path):
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()

    db = SimpleNamespace(
        get_roots=lambda: [],
        execute=lambda _sql: (_ for _ in ()).throw(RuntimeError("failed aggregate")),
    )
    indexer = SimpleNamespace(config=SimpleNamespace(workspace_roots=[str(ws_a)]))
    server = AsyncHttpServer(db, indexer, workspace_root=str(ws_a))

    monkeypatch.setattr(
        "sari.core.workspace.WorkspaceManager.resolve_config_path",
        lambda _root: str(tmp_path / "cfg.json"),
    )
    monkeypatch.setattr(
        "sari.core.config.main.Config.load",
        lambda _path, workspace_root_override=None: SimpleNamespace(workspace_roots=[str(ws_a)]),
    )

    resp = await server.workspaces(SimpleNamespace())
    data = json.loads(resp.body.decode("utf-8"))
    assert data["status_warning_counts"]["FAILED_TASKS_AGGREGATE_FAILED"] >= 1


def test_serve_async_marks_endpoint_not_ok_when_init_steps_fail(monkeypatch):
    class _FakeConfig:
        def __init__(self, app, host, port, log_level, access_log):
            self.app = app

    class _FakeServer:
        def __init__(self, _config):
            self.should_exit = False

        async def serve(self):
            raise RuntimeError("thread fail")

    class _ImmediateThread:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.root_id_for_workspace", lambda _r: (_ for _ in ()).throw(RuntimeError("rid fail")))
    monkeypatch.setattr("sari.mcp.server.LocalSearchMCPServer", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("mcp fail")))
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("uvicorn.Config", _FakeConfig)
    monkeypatch.setattr("uvicorn.Server", _FakeServer)

    uvicorn_server, _port = serve_async(
        host="127.0.0.1",
        port=49000,
        db=SimpleNamespace(),
        indexer=SimpleNamespace(cfg=SimpleNamespace(workspace_roots=["/tmp/ws"])),
        workspace_root="/tmp/ws",
        cfg=SimpleNamespace(),
        mcp_server=None,
    )

    assert getattr(uvicorn_server, "sari_endpoint_ok", True) is False
    assert "ROOT_IDS_RESOLVE_FAILED" in getattr(uvicorn_server, "sari_endpoint_errors", [])
    assert "MCP_SERVER_INIT_FAILED" in getattr(uvicorn_server, "sari_endpoint_errors", [])
    assert "ASYNC_SERVER_THREAD_FAILED" in getattr(uvicorn_server, "sari_endpoint_errors", [])
