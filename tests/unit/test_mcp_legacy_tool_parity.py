"""Batch-26 레거시 MCP 도구 이관 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer


def _register_repo(db_path: Path, repo_path: Path) -> None:
    """테스트용 워크스페이스를 등록한다."""
    init_schema(db_path)
    repo = WorkspaceRepository(db_path)
    repo.add(
        WorkspaceDTO(
            path=str(repo_path),
            name=repo_path.name,
            indexed_at=None,
            is_active=True,
        )
    )


def test_status_tool_returns_pack1_success(tmp_path: Path) -> None:
    """status 도구는 등록 repo 입력 시 성공 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    _register_repo(db_path, repo_path)
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {"repo": str(repo_path)}},
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    assert "items" in payload["result"]["structuredContent"]


def test_read_tool_requires_target(tmp_path: Path) -> None:
    """read 도구는 target 누락 시 명시적 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    _register_repo(db_path, repo_path)
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read", "arguments": {"repo": str(repo_path), "mode": "file"}},
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    first_error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert first_error["code"] == "ERR_TARGET_REQUIRED"


def test_save_snippet_and_get_snippet_roundtrip(tmp_path: Path) -> None:
    """save_snippet 저장 후 get_snippet 조회가 가능해야 한다."""
    db_path = tmp_path / "state.db"
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    _register_repo(db_path, repo_path)
    source_file = repo_path / "sample.py"
    source_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    server = McpServer(db_path=db_path)

    save_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "save_snippet",
                "arguments": {
                    "repo": str(repo_path),
                    "path": str(source_file),
                    "start_line": 1,
                    "end_line": 2,
                    "tag": "sample",
                },
            },
        }
    )
    save_payload = save_response.to_dict()
    assert save_payload["result"]["isError"] is False

    get_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_snippet",
                "arguments": {"repo": str(repo_path), "tag": "sample", "limit": 5},
            },
        }
    )
    get_payload = get_response.to_dict()
    assert get_payload["result"]["isError"] is False
    items = get_payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["tag"] == "sample"
