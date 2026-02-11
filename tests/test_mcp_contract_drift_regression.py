import logging
import time

from sari.core.models import IndexingResult
from sari.core.search_engine import SearchEngine
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.registry import ToolContext, build_default_registry
from sari.mcp.tools.list_symbols import execute_list_symbols
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints


def _seed_workspace_and_symbol(db, tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    file_path = ws_root / "api.py"
    file_path.write_text("def get_users():\n    return []\n", encoding="utf-8")

    rid = WorkspaceManager.root_id_for_workspace(str(ws_root))
    db.upsert_root(rid, str(ws_root), str(ws_root))
    db.upsert_files_turbo(
        [
            IndexingResult(
                path=f"{rid}/api.py",
                rel="api.py",
                root_id=rid,
                repo="repo1",
                type="new",
                content=file_path.read_text(encoding="utf-8"),
                fts_content=file_path.read_text(encoding="utf-8"),
                mtime=int(time.time()),
                size=file_path.stat().st_size,
                content_hash="h1",
                scan_ts=int(time.time()),
                metadata_json="{}",
            )
        ]
    )
    db.finalize_turbo_batch()

    conn = db.get_read_connection()
    conn.execute(
        """
        INSERT INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent, meta_json, doc_comment, qualname, importance_score)
        VALUES(:symbol_id,:path,:root_id,:name,:kind,:line,:end_line,:content,:parent,:meta_json,:doc_comment,:qualname,:importance_score)
        """,
        {
            "symbol_id": "sid-api-get-users",
            "path": f"{rid}/api.py",
            "root_id": rid,
            "name": "get_users",
            "kind": "function",
            "line": 1,
            "end_line": 2,
            "content": "def get_users():\n    return []",
            "parent": "",
            "meta_json": '{"http_path":"/api/users","annotations":["GET"]}',
            "doc_comment": "",
            "qualname": "get_users",
            "importance_score": 0.0,
        },
    )
    conn.commit()
    return ws_root, rid, file_path


def test_db_facade_contract_and_schema(db):
    assert hasattr(db, "apply_root_filter")
    assert hasattr(db, "count_failed_tasks")
    assert hasattr(db, "register_writer_thread")
    assert hasattr(db, "contexts")
    assert hasattr(db, "search_snippets")
    assert hasattr(db, "list_snippet_versions")
    assert hasattr(db, "update_snippet_location_tx")

    total_failed, high_failed = db.count_failed_tasks()
    assert isinstance(total_failed, int)
    assert isinstance(high_failed, int)

    row = db.db.connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='snippet_versions'"
    ).fetchone()
    assert row is not None


def test_drift_sensitive_tools_do_not_crash_on_schema_names(db, tmp_path):
    ws_root, _, file_path = _seed_workspace_and_symbol(db, tmp_path)
    roots = [str(ws_root)]

    list_symbols_res = execute_list_symbols({"path": str(file_path)}, db, roots)
    assert isinstance(list_symbols_res, dict)
    assert not list_symbols_res.get("isError", False)

    api_res = execute_search_api_endpoints({"path": "/api/users"}, db, roots)
    assert isinstance(api_res, dict)
    assert not api_res.get("isError", False)


def test_registry_smoke_for_contract_drift_tools(db, tmp_path):
    ws_root, rid, file_path = _seed_workspace_and_symbol(db, tmp_path)
    db.set_engine(SearchEngine(db))
    reg = build_default_registry()
    ctx = ToolContext(
        db=db,
        engine=db.engine,
        indexer=None,
        roots=[str(ws_root)],
        cfg=None,
        logger=logging.getLogger("tests.contract"),
        workspace_root=str(ws_root),
        server_version="test",
        policy_engine=None,
    )

    res_repo = reg.execute("search", ctx, {"query": "api", "search_type": "repo", "root_ids": [rid]})
    assert isinstance(res_repo, dict)
    assert not res_repo.get("isError", False)

    res_list = reg.execute("list_symbols", ctx, {"path": str(file_path)})
    assert isinstance(res_list, dict)
    assert not res_list.get("isError", False)

    res_api = reg.execute("search", ctx, {"query": "/api/users", "search_type": "api"})
    assert isinstance(res_api, dict)
    assert not res_api.get("isError", False)

    res_cg = reg.execute("call_graph_health", ctx, {})
    assert isinstance(res_cg, dict)
    assert not res_cg.get("isError", False)

    res_save = reg.execute(
        "save_snippet",
        ctx,
        {"path": str(file_path), "tag": "drift_tag", "start_line": 1, "end_line": 1},
    )
    assert isinstance(res_save, dict)
    assert not res_save.get("isError", False)

    res_get_snippet = reg.execute("get_snippet", ctx, {"tag": "drift_tag"})
    assert isinstance(res_get_snippet, dict)
    assert not res_get_snippet.get("isError", False)

    res_archive = reg.execute(
        "archive_context",
        ctx,
        {"topic": "drift-topic", "content": "drift-content"},
    )
    assert isinstance(res_archive, dict)
    assert not res_archive.get("isError", False)

    res_context = reg.execute("get_context", ctx, {"topic": "drift-topic"})
    assert isinstance(res_context, dict)
    assert not res_context.get("isError", False)
