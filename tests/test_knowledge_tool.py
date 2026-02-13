import hashlib
import json
from unittest.mock import MagicMock

import pytest

from sari.core.workspace import WorkspaceManager
from sari.mcp.tools.crypto import issue_context_ref
from sari.mcp.tools.knowledge import execute_knowledge
from sari.mcp.tools.registry import ToolContext, build_default_registry


def _json_payload(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def test_registry_exposes_knowledge_and_hides_legacy_by_default(monkeypatch):
    monkeypatch.delenv("SARI_EXPOSE_INTERNAL_TOOLS", raising=False)
    reg = build_default_registry()
    names = {tool["name"] for tool in reg.list_tools()}

    assert "knowledge" in names
    assert "save_snippet" not in names
    assert "archive_context" not in names
    assert "get_context" not in names
    assert "get_snippet" not in names


def test_registry_marks_legacy_knowledge_tools_deprecated_when_internal_exposed(monkeypatch):
    monkeypatch.setenv("SARI_EXPOSE_INTERNAL_TOOLS", "1")
    reg = build_default_registry()
    tools = {tool["name"]: tool for tool in reg.list_tools()}

    assert tools["save_snippet"]["deprecated"] is True
    assert tools["archive_context"]["deprecated"] is True
    assert tools["get_context"]["deprecated"] is True
    assert tools["get_snippet"]["deprecated"] is True


def test_knowledge_recall_accepts_search_alias_once_with_warning(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    db = MagicMock()
    db.contexts.search_contexts.return_value = []

    resp = execute_knowledge({"action": "search", "type": "context", "query": "auth"}, db, ["/tmp/ws"])
    payload = _json_payload(resp)

    assert payload.get("isError") is not True
    assert payload["action"] == "recall"
    assert "ACTION_ALIAS_DEPRECATED: use recall" in payload.get("warnings", [])


def test_knowledge_save_requires_context_ref(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    db = MagicMock()

    resp = execute_knowledge({"action": "save", "type": "context", "key": "topic-a", "content": "memo"}, db, ["/tmp/ws"])
    payload = _json_payload(resp)

    assert payload["isError"] is True
    assert payload["error"]["code"] == "INVALID_ARGS"


def test_knowledge_save_context_persists_when_context_ref_is_valid(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    root = "/tmp/ws"
    root_id = WorkspaceManager.root_id_for_workspace(root)
    content = "critical context"

    token = issue_context_ref(
        {
            "ws": root_id,
            "kind": "file",
            "path": f"{root_id}/src/app.py",
            "span": [1, 3],
            "ch": _sha12(content),
        }
    )

    db = MagicMock()
    ctx_row = MagicMock()
    ctx_row.model_dump.return_value = {"topic": "topic-a", "content": content}
    db.contexts.upsert.return_value = ctx_row

    resp = execute_knowledge(
        {
            "action": "save",
            "type": "context",
            "context_ref": token,
            "key": "topic-a",
            "content": content,
            "labels": ["release"],
        },
        db,
        [root],
    )
    payload = _json_payload(resp)

    assert payload.get("isError") is not True
    assert payload["action"] == "save"
    assert payload["type"] == "context"
    assert payload["saved"]["topic"] == "topic-a"


def test_knowledge_save_rejects_expired_context_ref(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    root = "/tmp/ws"
    root_id = WorkspaceManager.root_id_for_workspace(root)
    content = "stale"

    token = issue_context_ref(
        {
            "ws": root_id,
            "kind": "file",
            "path": f"{root_id}/src/app.py",
            "span": [1, 1],
            "ch": _sha12(content),
        },
        ttl_seconds=-1,
    )

    db = MagicMock()
    resp = execute_knowledge(
        {
            "action": "save",
            "type": "context",
            "context_ref": token,
            "key": "topic-stale",
            "content": content,
        },
        db,
        [root],
    )
    payload = _json_payload(resp)

    assert payload["isError"] is True
    assert payload["error"]["code"] == "INVALID_ARGS"
    assert "expired" in payload["error"]["message"].lower()


def test_knowledge_list_delete_and_relink_context(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    root = "/tmp/ws"
    root_id = WorkspaceManager.root_id_for_workspace(root)

    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 1
    conn.cursor.return_value = cur
    row = {"topic": "topic-a", "content": "x", "deprecated": 0, "updated_ts": 1}
    conn.execute.return_value.fetchall.return_value = [row]
    db.get_read_connection.return_value = conn
    db._write = conn
    db.contexts.get_context_by_topic.return_value = MagicMock(related_files=[], topic="topic-a")

    list_resp = execute_knowledge({"action": "list", "type": "context", "limit": 5}, db, [root])
    list_payload = _json_payload(list_resp)
    assert list_payload["count"] == 1
    assert list_payload["results"][0]["memory_ref"] == "context:topic-a"

    delete_resp = execute_knowledge({"action": "delete", "type": "context", "memory_ref": "context:topic-a"}, db, [root])
    delete_payload = _json_payload(delete_resp)
    assert delete_payload["deleted"] >= 0

    token = issue_context_ref(
        {
            "ws": root_id,
            "kind": "file",
            "path": f"{root_id}/src/new.py",
            "span": [2, 4],
            "ch": _sha12("abc"),
        }
    )
    relink_resp = execute_knowledge(
        {
            "action": "relink",
            "type": "context",
            "memory_ref": "context:topic-a",
            "new_context_ref": token,
        },
        db,
        [root],
    )
    relink_payload = _json_payload(relink_resp)
    assert relink_payload["type"] == "context"
    assert relink_payload["memory_ref"] == "context:topic-a"


def test_legacy_knowledge_tools_are_blocked_and_guide_to_unified_tool(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reg = build_default_registry()
    ctx = ToolContext(
        db=MagicMock(),
        engine=None,
        indexer=MagicMock(),
        roots=["/tmp/ws"],
        cfg=MagicMock(),
        logger=MagicMock(),
        workspace_root="/tmp/ws",
        server_version="test",
    )

    for tool_name in ("save_snippet", "get_snippet", "archive_context", "get_context"):
        res = reg.execute(tool_name, ctx, {})
        payload = _json_payload(res)
        assert payload["isError"] is True
        assert payload["error"]["code"] == "INVALID_ARGS"
        assert "Use knowledge(" in payload["error"]["message"]
