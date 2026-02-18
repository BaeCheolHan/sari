"""strict session 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer


def test_read_requires_session_id_when_strict_enabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """strict session 정책 활성화 시 session_id 누락 요청은 실패해야 한다."""
    monkeypatch.setenv("SARI_STRICT_SESSION_ID", "1")
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=str(repo_path), name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 701,
            "method": "tools/call",
            "params": {
                "name": "read",
                    "arguments": {
                        "repo": str(repo_path),
                        "mode": "diff_preview",
                        "target": "main.py",
                        "content": "print('b')\n",
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert error["code"] == "ERR_SESSION_ID_REQUIRED"
