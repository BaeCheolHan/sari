import json
import pytest
from types import SimpleNamespace
from starlette.datastructures import QueryParams

from sari.core.async_http_server import AsyncHttpServer


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
