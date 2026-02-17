"""MCP 파이프라인 운영 도구 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.services.workspace_service import WorkspaceService


def test_mcp_pipeline_policy_get_returns_policy_payload(tmp_path: Path) -> None:
    """pipeline_policy_get 도구는 정책 정보를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 70,
            "method": "tools/call",
            "params": {
                "name": "pipeline_policy_get",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    structured = payload["result"]["structuredContent"]
    items = structured["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert "deletion_hold" in items[0]


def test_mcp_pipeline_dead_requeue_requires_repo(tmp_path: Path) -> None:
    """pipeline_dead_requeue 도구는 repo 인자를 필수로 요구해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 71,
            "method": "tools/call",
            "params": {
                "name": "pipeline_dead_requeue",
                "arguments": {},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_REPO_REQUIRED"


def test_mcp_pipeline_auto_status_returns_state(tmp_path: Path) -> None:
    """pipeline_auto_status 도구는 자동제어 상태를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 72,
            "method": "tools/call",
            "params": {
                "name": "pipeline_auto_status",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert "auto_hold_enabled" in items[0]


def test_mcp_pipeline_dead_requeue_supports_all_flag(tmp_path: Path) -> None:
    """pipeline_dead_requeue는 all=true로 전체 DEAD 작업을 재큐잉할 수 있어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir.resolve()))
    queue_repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T10:00:00+00:00"
    for index in range(3):
        job_id = queue_repo.enqueue(str(repo_dir.resolve()), f"f{index}.py", f"h{index}", 30, "scan", now_iso)
        queue_repo.mark_failed_with_backoff(job_id, "e", now_iso, dead_threshold=1, backoff_base_sec=1)
    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 73,
            "method": "tools/call",
            "params": {
                "name": "pipeline_dead_requeue",
                "arguments": {"repo": str(repo_dir.resolve()), "all": True, "limit": 1},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    assert payload["result"]["structuredContent"]["requeued_count"] == 3
