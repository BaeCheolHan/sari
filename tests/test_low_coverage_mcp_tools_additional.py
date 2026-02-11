from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sari.mcp.tools.call_graph_health import _load_plugins, execute_call_graph_health
from sari.mcp.tools.call_graph import execute_call_graph
from sari.mcp.tools.archive_context import execute_archive_context
from sari.mcp.tools.get_context import execute_get_context
from sari.mcp.tools.get_callers import execute_get_callers
from sari.mcp.tools.list_files import execute_list_files
from sari.mcp.tools.list_symbols import execute_list_symbols
from sari.mcp.tools.read_file import execute_read_file
from sari.mcp.tools.repo_candidates import execute_repo_candidates
from sari.mcp.tools.rescan import execute_rescan
from sari.mcp.tools.scan_once import execute_scan_once
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.search import _clip_text, execute_search
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.registry import Tool, ToolContext, ToolRegistry, build_default_registry


class _CtxRow:
    def __init__(self, topic: str, updated_ts: int, deprecated: int) -> None:
        self.topic = topic
        self.updated_ts = updated_ts
        self.deprecated = deprecated

    def model_dump(self):
        return {
            "topic": self.topic,
            "updated_ts": self.updated_ts,
            "deprecated": self.deprecated,
        }


def _assert_invalid_args_response(resp):
    assert resp.get("isError") is True
    err = resp.get("error")
    if isinstance(err, dict):
        assert err.get("code") == "INVALID_ARGS"
        return
    text = resp["content"][0]["text"] if "content" in resp else str(resp)
    assert "code=INVALID_ARGS" in text


def test_search_private_clip_text_edges():
    assert _clip_text("abc", 0) == ""
    assert _clip_text("abcdef", 3) == "abc"


def test_search_parse_error_returns_invalid_args(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()

    resp = execute_search({"query": "x", "limit": "bad"}, db, MagicMock(), ["/tmp/ws"])

    text = resp["content"][0]["text"] if "content" in resp else str(resp)
    assert resp.get("isError") is True
    assert "code=INVALID_ARGS" in text
    assert resp.get("isError") is True


def test_search_db_error_returns_engine_query(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()
    db.search.side_effect = RuntimeError("boom")

    resp = execute_search({"query": "x"}, db, MagicMock(), ["/tmp/ws"])

    text = resp["content"][0]["text"] if "content" in resp else str(resp)
    assert resp.get("isError") is True
    assert "boom" in text


def test_search_json_and_importance_tags(monkeypatch):
    # JSON branch: v3 normalized response contract
    monkeypatch.setenv("SARI_FORMAT", "json")
    db = MagicMock()
    obj_hit = SimpleNamespace(
        path="p2.py",
        repo="r2",
        score=2.0,
        mtime=0,
        size=100,
        file_type="py",
        snippet="B" * 500,
        hit_reason="score(importance=3.1)",
    )
    obj_hit2 = SimpleNamespace(
        path="p1.py",
        repo="r1",
        score=1.0,
        mtime=0,
        size=100,
        file_type="py",
        snippet="A" * 500,
        hit_reason="score(importance=11.2)",
    )
    db.search.return_value = ([obj_hit2, obj_hit], {"total": 2, "engine": "embedded"})

    resp = execute_search(
        {"query": "x", "search_type": "code", "limit": 10, "max_preview_chars": 120},
        db,
        MagicMock(),
        ["/tmp/ws"],
    )
    payload = resp if "matches" in resp else json.loads(resp["content"][0]["text"]) if "content" in resp else resp

    assert payload["ok"] is True
    assert payload["mode"] == "code"
    assert len(payload["matches"]) == 2
    assert len(payload["matches"][0]["snippet"]) <= 120
    assert payload["meta"]["total"] == 2

    # PACK branch: ensure core search rows are emitted in unified format.
    monkeypatch.setenv("SARI_FORMAT", "pack")
    pack_hit1 = SimpleNamespace(
        path="core.py",
        repo="r",
        score=1.0,
        mtime=0,
        size=10,
        file_type="py",
        snippet="x",
        hit_reason="h(importance=11)",
    )
    pack_hit2 = SimpleNamespace(
        path="sig.py",
        repo="r",
        score=1.0,
        mtime=0,
        size=10,
        file_type="py",
        snippet="x",
        hit_reason="h(importance=3)",
    )
    pack_hit3 = SimpleNamespace(
        path="bad.py",
        repo="r",
        score=1.0,
        mtime=0,
        size=10,
        file_type="py",
        snippet="x",
        hit_reason="h(importance=abc)",
    )
    db.search.return_value = (
        [pack_hit1, pack_hit2, pack_hit3],
        {"total": 3, "engine": "embedded"},
    )
    pack_resp = execute_search({"query": "x", "search_type": "code", "limit": 10}, db, MagicMock(), ["/tmp/ws"])
    text = pack_resp["content"][0]["text"]

    assert "PACK1 tool=search ok=true" in text
    assert "r:t=code p=core.py" in text
    assert "r:t=code p=sig.py" in text
    assert "r:t=code p=bad.py" in text


def test_get_context_requires_topic_or_query(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()

    resp = execute_get_context({}, db, ["/tmp/ws"])

    text = resp["content"][0]["text"] if "content" in resp else str(resp)
    assert "PACK1 tool=get_context ok=false" in text
    assert "code=INVALID_ARGS" in text


def test_get_context_rejects_non_object_args():
    resp = execute_get_context(["bad-args"], MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_get_context_invalid_limit_is_handled():
    resp = execute_get_context({"query": "q", "limit": "bad"}, MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_get_context_topic_and_query_paths(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    db = MagicMock()
    row = _CtxRow("t1", 123, 0)
    db.contexts.get_context_by_topic.return_value = row

    topic_resp = execute_get_context({"topic": "t1"}, db, ["/tmp/ws"])
    topic_payload = json.loads(topic_resp["content"][0]["text"])
    assert topic_payload["count"] == 1
    assert topic_payload["results"][0]["topic"] == "t1"

    db.contexts.search_contexts.side_effect = RuntimeError("ctx-fail")
    err_resp = execute_get_context({"query": "q"}, db, ["/tmp/ws"])
    err_payload = json.loads(err_resp["content"][0]["text"])
    assert err_payload["error"]["code"] == "DB_ERROR"


def test_list_files_summary_and_json_detail(monkeypatch):
    db = MagicMock()
    db.get_repo_stats.return_value = {"repo1": 2}
    db.list_files.return_value = [{"path": "a.py", "repo": "repo1", "size": 10}]

    monkeypatch.setenv("SARI_FORMAT", "pack")
    summary_resp = execute_list_files({}, db, MagicMock(), ["/tmp/ws"])
    summary_text = summary_resp["content"][0]["text"]
    assert "mode=summary" in summary_text
    assert "f:path=a.py repo=repo1" in summary_text

    monkeypatch.setenv("SARI_FORMAT", "json")
    detail_resp = execute_list_files({"repo": "repo1", "limit": 1}, db, MagicMock(), ["/tmp/ws"])
    detail_payload = json.loads(detail_resp["content"][0]["text"])
    assert detail_payload["repo"] == "repo1"
    assert detail_payload["files"][0]["path"] == "a.py"


def test_list_files_invalid_limit_is_handled(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()

    resp = execute_list_files({"limit": "bad"}, db, MagicMock(), ["/tmp/ws"])

    text = resp["content"][0]["text"] if "content" in resp else str(resp)
    assert "PACK1 tool=list_files ok=false" in text
    assert "code=INVALID_ARGS" in text


def test_call_graph_health_plugin_loading(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_CALLGRAPH_PLUGIN", "json,not_a_real_plugin_zzz")

    assert _load_plugins() == ["json", "not_a_real_plugin_zzz"]

    resp = execute_call_graph_health({}, MagicMock())
    payload = resp if "matches" in resp else json.loads(resp["content"][0]["text"]) if "content" in resp else resp

    statuses = {p["name"]: p["status"] for p in payload["plugins"]}
    assert statuses["json"] == "loaded"
    assert statuses["not_a_real_plugin_zzz"].startswith("error:")


def test_call_graph_health_rejects_non_object_args():
    resp = execute_call_graph_health(["bad-args"], MagicMock())
    _assert_invalid_args_response(resp)


def test_list_files_rejects_non_object_args():
    resp = execute_list_files(["bad-args"], MagicMock(), MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_rescan_and_scan_once_error_paths(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")

    class _Svc:
        def __init__(self, _indexer):
            pass

        def rescan(self):
            from sari.mcp.tools._util import ErrorCode
            return {"ok": False, "code": ErrorCode.ERR_INDEXER_DISABLED, "message": "disabled", "data": {"mode": "off"}}

        def scan_once(self):
            from sari.mcp.tools._util import ErrorCode
            return {"ok": False, "code": ErrorCode.ERR_INDEXER_FOLLOWER, "message": "follower", "data": {"mode": "follower"}}

    monkeypatch.setattr("sari.mcp.tools.rescan.IndexService", _Svc)
    monkeypatch.setattr("sari.mcp.tools.scan_once.IndexService", _Svc)

    r1 = execute_rescan({}, MagicMock())
    t1 = r1["content"][0]["text"]
    assert "tool=rescan ok=false" in t1
    assert "code=ERR_INDEXER_DISABLED" in t1

    r2 = execute_scan_once({}, MagicMock(), MagicMock())
    t2 = r2["content"][0]["text"]
    assert "tool=scan_once ok=false" in t2
    assert "code=ERR_INDEXER_FOLLOWER" in t2


def test_repo_candidates_invalid_query_and_limit_fallback(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()

    bad = execute_repo_candidates({"query": "   "}, db, MagicMock(), ["/tmp/ws"])
    assert "code=INVALID_ARGS" in bad["content"][0]["text"]

    db.repo_candidates.return_value = [{"repo": "r1", "score": 11}]
    ok = execute_repo_candidates({"query": "x", "limit": "bad"}, db, MagicMock(), ["/tmp/ws"])
    text = ok["content"][0]["text"]
    assert "tool=repo_candidates ok=true" in text
    assert "reason=High%20match" in text


def test_repo_candidates_clamps_negative_limit_to_one(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    db = MagicMock()
    db.repo_candidates.return_value = [{"repo": "r1", "score": 1}]
    execute_repo_candidates({"query": "x", "limit": -5}, db, MagicMock(), ["/tmp/ws"])
    _, kwargs = db.repo_candidates.call_args
    assert kwargs["limit"] == 1


def test_repo_candidates_rejects_non_object_args():
    resp = execute_repo_candidates(["bad-args"], MagicMock(), MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_read_file_error_and_json_metadata_paths(monkeypatch, tmp_path):
    db = MagicMock()

    monkeypatch.setenv("SARI_FORMAT", "pack")
    missing_path_resp = execute_read_file({}, db, [str(tmp_path)])
    assert "code=INVALID_ARGS" in missing_path_resp["content"][0]["text"]

    # resolve_db_path miss + file absent on disk => NOT_INDEXED
    monkeypatch.setattr("sari.mcp.tools.read_file.resolve_db_path", lambda *_args, **_kwargs: None)
    absent_resp = execute_read_file({"path": str(tmp_path / "absent.py")}, db, [str(tmp_path)])
    assert "code=NOT_INDEXED" in absent_resp["content"][0]["text"]

    # DB hit but content missing => NOT_INDEXED via _read_file_content
    monkeypatch.setattr("sari.mcp.tools.read_file.resolve_db_path", lambda *_args, **_kwargs: "rid/a.py")
    db.read_file.return_value = None
    not_indexed_resp = execute_read_file({"path": "a.py"}, db, [str(tmp_path)])
    assert "code=NOT_INDEXED" in not_indexed_resp["content"][0]["text"]

    # JSON branch with truncation + high-token warning
    monkeypatch.setenv("SARI_FORMAT", "json")
    db.read_file.return_value = "line1\nline2\nline3\nline4"
    monkeypatch.setattr("sari.mcp.tools.read_file._count_tokens", lambda _c: 3001)
    json_resp = execute_read_file({"path": "a.py", "offset": 1, "limit": 2}, db, [str(tmp_path)])
    payload = json.loads(json_resp["content"][0]["text"])
    assert payload["metadata"]["is_truncated"] is True
    assert payload["metadata"]["efficiency_warning"] == "High token usage"


def test_list_symbols_rejects_non_object_args():
    resp = execute_list_symbols(["bad-args"], MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_read_file_invalid_offset_limit_types_are_handled(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()

    bad_offset = execute_read_file({"path": str(tmp_path / "a.py"), "offset": "bad"}, db, [str(tmp_path)])
    assert "code=INVALID_ARGS" in bad_offset["content"][0]["text"]

    bad_limit = execute_read_file({"path": str(tmp_path / "a.py"), "limit": "bad"}, db, [str(tmp_path)])
    assert "code=INVALID_ARGS" in bad_limit["content"][0]["text"]

    negative = execute_read_file({"path": str(tmp_path / "a.py"), "offset": -1}, db, [str(tmp_path)])
    assert "code=INVALID_ARGS" in negative["content"][0]["text"]


def test_unified_read_rejects_against_for_non_diff_mode():
    import urllib.parse

    resp = execute_read({"mode": "file", "target": "a.py", "against": "HEAD"}, MagicMock(), ["/tmp/ws"])
    text = urllib.parse.unquote(resp["content"][0]["text"])
    assert "code=INVALID_ARGS" in text or '"code":"INVALID_ARGS"' in text
    assert "against is only valid for mode='diff_preview'. Remove it or switch mode." in text


def test_unified_read_rejects_snippet_args_for_non_snippet_mode():
    import urllib.parse

    resp = execute_read({"mode": "file", "target": "a.py", "start_line": 1}, MagicMock(), ["/tmp/ws"])
    text = urllib.parse.unquote(resp["content"][0]["text"])
    assert "code=INVALID_ARGS" in text or '"code":"INVALID_ARGS"' in text
    assert "start_line is only valid for mode='snippet'. Remove it or switch mode." in text


def test_unified_read_rejects_symbol_disambiguation_args_for_non_symbol_mode():
    import urllib.parse

    resp = execute_read({"mode": "file", "target": "a.py", "symbol_id": "sid-1"}, MagicMock(), ["/tmp/ws"])
    text = urllib.parse.unquote(resp["content"][0]["text"])
    assert "code=INVALID_ARGS" in text or '"code":"INVALID_ARGS"' in text
    assert "symbol_id is only valid for mode='symbol'. Remove it or switch mode." in text


def test_unified_read_rejects_invalid_against_enum_value():
    import urllib.parse

    resp = execute_read(
        {"mode": "diff_preview", "target": "a.py", "content": "x", "against": "BAD"},
        MagicMock(),
        ["/tmp/ws"],
    )
    text = urllib.parse.unquote(resp["content"][0]["text"])
    assert "code=INVALID_ARGS" in text or '"code":"INVALID_ARGS"' in text
    assert "'against' must be one of: HEAD, WORKTREE, INDEX" in text


def test_call_graph_list_logger_roots_and_db_error(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")

    class _SvcOK:
        def __init__(self, _db, roots):
            self.roots = roots

        def build(self, _args):
            return {"symbol": "S", "tree": "T", "meta": {"nodes": 1, "edges": 0}, "truncated": False, "graph_quality": "good"}

    monkeypatch.setattr("sari.mcp.tools.call_graph.CallGraphService", _SvcOK)
    ok = execute_call_graph({"symbol": "S"}, MagicMock(), ["/tmp/ws"])
    assert "PACK1 tool=call_graph ok=true" in ok["content"][0]["text"]

    class _SvcErr:
        def __init__(self, _db, _roots):
            pass

        def build(self, _args):
            raise RuntimeError("db exploded")

    monkeypatch.setattr("sari.mcp.tools.call_graph.CallGraphService", _SvcErr)
    bad = execute_call_graph({"symbol": "S"}, MagicMock(), MagicMock(), ["/tmp/ws"])
    assert "code=DB_ERROR" in bad["content"][0]["text"]


def test_call_graph_rejects_non_object_args():
    resp = execute_call_graph(["bad-args"], MagicMock(), MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_archive_context_rejects_non_object_args():
    resp = execute_archive_context(["bad-args"], MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_get_callers_sid_repo_and_invalid_limit(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")

    class _Conn:
        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return self

        def fetchall(self):
            return [("rid/a.py", "caller_fn", "sid-caller", 10, "calls")]

    conn = _Conn()

    class _DB:
        def get_read_connection(self):
            return conn

    monkeypatch.setattr("sari.mcp.tools.get_callers.resolve_root_ids", lambda _roots: ["rid"])
    monkeypatch.setattr("sari.mcp.tools.get_callers.resolve_repo_scope", lambda _repo, _roots, db=None: ("repo1", ["rid"]))
    out = execute_get_callers({"symbol_id": "sid-target", "repo": "repo1", "root_ids": ["rid"], "limit": 1}, _DB(), ["/tmp/ws"])
    payload = json.loads(out["content"][0]["text"])
    assert payload["count"] == 1
    assert "to_symbol_id = ?" in conn.sql
    assert conn.params[0] == "sid-target"

    monkeypatch.setenv("SARI_FORMAT", "pack")
    # Expected graceful error response for bad limit
    bad = execute_get_callers({"name": "x", "limit": "bad"}, _DB(), ["/tmp/ws"])
    assert "code=INVALID_ARGS" in bad["content"][0]["text"]


def test_search_api_endpoints_invalid_args_and_filters(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    db = MagicMock()
    bad = execute_search_api_endpoints({}, db, ["/tmp/ws"])
    assert "code=INVALID_ARGS" in bad["content"][0]["text"]

    class _Conn:
        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            return self

        def fetchall(self):
            return [
                {"path": "rid/u.py", "name": "UsersAPI", "kind": "class", "line": 1, "metadata": '{"http_path":"/api/users"}', "content": "class UsersAPI", "repo": "repo1"},
                {"path": "rid/bad.py", "name": "Bad", "kind": "function", "line": 1, "metadata": "{", "content": "def bad(): pass", "repo": "repo1"},
            ]

    conn = _Conn()
    db.get_read_connection.return_value = conn
    monkeypatch.setattr("sari.mcp.tools.search_api_endpoints.resolve_root_ids", lambda _roots: ["rid"])
    ok = execute_search_api_endpoints({"path": "/api", "repo": "repo1"}, db, ["/tmp/ws"])
    text = ok["content"][0]["text"]
    assert "PACK1 tool=search_api_endpoints ok=true" in text
    assert "path=rid/u.py" in text
    assert "rid/bad.py" not in text
    assert "s.path LIKE ?" in conn.sql


def test_search_api_endpoints_rejects_non_object_args():
    resp = execute_search_api_endpoints(["bad-args"], MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_search_symbols_rejects_non_object_args():
    resp = execute_search_symbols(["bad-args"], MagicMock(), MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_search_symbols_invalid_limit_is_handled():
    resp = execute_search_symbols({"query": "x", "limit": "bad"}, MagicMock(), MagicMock(), ["/tmp/ws"])
    _assert_invalid_args_response(resp)


def test_registry_execute_policy_and_guard_paths(monkeypatch):
    reg = ToolRegistry()
    reg.register(Tool(name="search", description="d", input_schema={}, handler=lambda _ctx, _args: {"content": [123]}))
    reg.register(Tool(name="other", description="d", input_schema={}, handler=lambda _ctx, _args: {"content": [{"text": "PACK1 tool=other ok=false code=INVALID_ARGS msg=x"}]}))

    policy = MagicMock()
    ctx = ToolContext(
        db=MagicMock(),
        engine=None,
        indexer=MagicMock(),
        roots=["/tmp/ws"],
        cfg=MagicMock(),
        logger=MagicMock(),
        workspace_root="/tmp/ws",
        server_version="test",
        policy_engine=policy,
    )

    reg.execute("search", ctx, {})
    policy.mark_action.assert_called_once_with("search")

    reg.execute("other", ctx, {})
    assert policy.mark_action.call_count == 1

    with pytest.raises(ValueError):
        reg.execute("missing", ctx, {})

    # list_tools_raw branch
    assert len(reg.list_tools_raw()) == 2

    monkeypatch.setenv("SARI_EXPOSE_INTERNAL_TOOLS", "1")
    names = {t["name"] for t in build_default_registry().list_tools()}
    assert "scan_once" in names
    assert "rescan" in names


def test_registry_execute_does_not_mark_action_for_whitespace_prefixed_pack_error():
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="search",
            description="d",
            input_schema={},
            handler=lambda _ctx, _args: {
                "content": [{"text": "  PACK1 tool=search ok=false code=INVALID_ARGS msg=x"}]
            },
        )
    )

    policy = MagicMock()
    ctx = ToolContext(
        db=MagicMock(),
        engine=None,
        indexer=MagicMock(),
        roots=["/tmp/ws"],
        cfg=MagicMock(),
        logger=MagicMock(),
        workspace_root="/tmp/ws",
        server_version="test",
        policy_engine=policy,
    )

    reg.execute("search", ctx, {})
    policy.mark_action.assert_not_called()
