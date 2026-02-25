"""MCP 파일 수집 도구(scan_once/list_files/read_file/index_file)를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.mcp.server import McpServer
from sari.services.workspace.service import WorkspaceService


def test_mcp_tools_list_includes_file_collection_tools(tmp_path: Path) -> None:
    """tools/list 응답은 파일 수집 도구 4종을 포함해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 21, "method": "tools/list"})
    payload = list_response.to_dict()
    tools = payload["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert "scan_once" in names
    assert "list_files" in names
    assert "read_file" in names
    assert "index_file" in names


def test_mcp_file_collection_scan_list_read_flow(tmp_path: Path) -> None:
    """scan_once 이후 list_files/read_file가 pack1 성공 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha():\n    return 7\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": str(repo_dir.resolve()), "options": {"structured": 1}},
            },
        }
    )
    scan_payload = scan_response.to_dict()
    assert scan_payload["result"]["isError"] is False

    list_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"repo": str(repo_dir.resolve()), "limit": 10, "options": {"structured": 1}},
            },
        }
    )
    list_payload = list_response.to_dict()
    assert list_payload["result"]["isError"] is False
    items = list_payload["result"]["structuredContent"]["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["relative_path"] == "alpha.py"

    read_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "relative_path": "alpha.py",
                    "offset": 0,
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    read_payload = read_response.to_dict()
    assert read_payload["result"]["isError"] is False
    read_items = read_payload["result"]["structuredContent"]["items"]
    assert isinstance(read_items, list)
    assert len(read_items) == 1
    assert "alpha" in read_items[0]["content"]


def test_mcp_index_file_requires_relative_path(tmp_path: Path) -> None:
    """index_file은 relative_path 누락 시 명시적 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 25,
            "method": "tools/call",
            "params": {
                "name": "index_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_RELATIVE_PATH_REQUIRED"


def test_mcp_read_file_returns_explicit_error_for_corrupted_l2_body(tmp_path: Path) -> None:
    """L2 본문 손상 시 read_file은 명시적 오류 응답을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-corrupt"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha():\n    return 7\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 210,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": str(repo_dir.resolve())},
            },
        }
    )
    assert scan_response.to_dict()["result"]["isError"] is False

    with connect(db_path) as conn:
        file_row = conn.execute(
            """
            SELECT content_hash
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND relative_path = :relative_path
            """,
            {"repo_root": str(repo_dir.resolve()), "relative_path": "alpha.py"},
        ).fetchone()
        assert file_row is not None

        conn.execute(
            """
            INSERT INTO collected_file_bodies_l2(
                repo_root, relative_path, content_hash, content_zlib, content_len,
                normalized_text, created_at, updated_at
            )
            VALUES(
                :repo_root, :relative_path, :content_hash, :content_zlib, :content_len,
                :normalized_text, :created_at, :updated_at
            )
            """,
            {
                "repo_root": str(repo_dir.resolve()),
                "relative_path": "alpha.py",
                "content_hash": str(file_row["content_hash"]),
                "content_zlib": b"corrupted-zlib",
                "content_len": 25,
                "normalized_text": "broken",
                "created_at": "2026-02-16T00:00:00+00:00",
                "updated_at": "2026-02-16T00:00:00+00:00",
            },
        )
        conn.commit()

    read_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 211,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "relative_path": "alpha.py",
                    "offset": 0,
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = read_response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert error["code"] == "ERR_L2_BODY_CORRUPT"


def test_mcp_index_file_returns_explicit_error_for_non_collectible_path(tmp_path: Path) -> None:
    """index_file은 수집 정책 비대상 파일을 명시적으로 거부해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-non-collectible"
    repo_dir.mkdir()
    git_dir = repo_dir / ".git"
    git_dir.mkdir()
    (git_dir / "FETCH_HEAD").write_text("dummy", encoding="utf-8")

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 212,
            "method": "tools/call",
            "params": {
                "name": "index_file",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "relative_path": ".git/FETCH_HEAD",
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert error["code"] == "ERR_FILE_NOT_COLLECTIBLE"


def test_mcp_scan_once_fanout_workspace_top_level_repos(tmp_path: Path) -> None:
    """workspace 컨테이너 scan_once 1회로 top-level repo들이 각각 수집되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    workspace_dir = tmp_path / "study"
    workspace_dir.mkdir()
    repo_a = workspace_dir / "sari"
    repo_a.mkdir()
    (repo_a / "pyproject.toml").write_text("[project]\nname='sari'\n", encoding="utf-8")
    (repo_a / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    repo_b = workspace_dir / "serena"
    repo_b.mkdir()
    (repo_b / "package.json").write_text('{"name":"serena"}', encoding="utf-8")
    (repo_b / "beta.ts").write_text("export const beta = 2;\n", encoding="utf-8")

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(workspace_dir))
    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 300,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": workspace_dir.name, "options": {"structured": 1}},
            },
        }
    )
    scan_payload = scan_response.to_dict()
    assert scan_payload["result"]["isError"] is False

    scan_item = scan_payload["result"]["structuredContent"]["items"][0]
    assert scan_item["mode"] == "fanout_top_level"
    assert scan_item["target_repo_count"] == 2
    assert scan_item["succeeded_repo_count"] == 2
    assert scan_item["failed_repo_count"] == 0

    list_a = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 301,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"repo": "sari", "limit": 10, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert list_a["result"]["isError"] is False
    items_a = list_a["result"]["structuredContent"]["items"]
    paths_a = {str(item["relative_path"]) for item in items_a}
    assert "alpha.py" in paths_a

    list_b = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 302,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"repo": "serena", "limit": 10, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert list_b["result"]["isError"] is False
    items_b = list_b["result"]["structuredContent"]["items"]
    paths_b = {str(item["relative_path"]) for item in items_b}
    assert "beta.ts" in paths_b


def test_mcp_scan_once_single_child_repo_does_not_fanout(tmp_path: Path) -> None:
    """workspace 하위 후보가 1개면 단일 repo 스캔으로 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    workspace_dir = tmp_path / "study"
    workspace_dir.mkdir()
    dataset_dir = workspace_dir / "benchmark_dataset"
    dataset_dir.mkdir()
    (dataset_dir / "bench_0.py").write_text("def bench_0():\n    return 0\n", encoding="utf-8")

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(workspace_dir))
    server = McpServer(db_path=db_path)

    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 303,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": workspace_dir.name, "options": {"structured": 1}},
            },
        }
    )
    payload = scan_response.to_dict()
    assert payload["result"]["isError"] is False
    scan_item = payload["result"]["structuredContent"]["items"][0]
    assert scan_item["mode"] == "single_repo"


def test_mcp_list_files_rejects_inactive_workspace(tmp_path: Path) -> None:
    """비활성 workspace는 파일 조회 도구 호출이 차단되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-inactive"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha():\n    return 7\n", encoding="utf-8")

    workspace_repo = WorkspaceRepository(db_path)
    WorkspaceService(workspace_repo).add_workspace(str(repo_dir))
    workspace_repo.set_active(str(repo_dir.resolve()), False)

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 399,
            "method": "tools/call",
            "params": {
                "name": "list_files",
                "arguments": {"repo": str(repo_dir.resolve()), "limit": 10, "options": {"structured": 1}},
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["meta"]["errors"][0]
    assert error["code"] == "ERR_WORKSPACE_INACTIVE"
