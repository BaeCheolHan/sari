"""MCP read stabilization 메타를 검증한다."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sari.core.models import WorkspaceDTO
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect
from sari.db.schema import init_schema
from sari.mcp.server import McpServer
from sari.mcp.tools.tool_common import content_hash


def test_read_diff_preview_includes_stabilization_meta(tmp_path: Path) -> None:
    """read(diff_preview) 성공 응답은 stabilization 메타를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)
    scan_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6011,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root},
            },
        }
    ).to_dict()
    assert scan_response["result"]["isError"] is False

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 601,
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
    assert payload["result"]["isError"] is False
    meta = payload["result"]["structuredContent"]["meta"]
    stabilization = meta["stabilization"]
    assert stabilization["budget_state"] == "NORMAL"
    assert isinstance(stabilization["reason_codes"], list)
    assert isinstance(stabilization["evidence_refs"], list)
    assert len(stabilization["evidence_refs"]) == 1


def test_read_normalizes_mode_and_path_alias(tmp_path: Path) -> None:
    """read는 file_preview/path 별칭 입력을 file/target으로 정규화해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)
    scan_payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6011,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert scan_payload["result"]["isError"] is False

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 602,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": repo_root,
                    "mode": "file_preview",
                    "path": "main.py",
                    "offset": 0,
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["relative_path"] == "main.py"


def test_read_missing_target_returns_self_describing_error(tmp_path: Path) -> None:
    """read(file) target 누락 오류는 expected/example 힌트를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 603,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": repo_root,
                    "mode": "file",
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is True
    error = payload["result"]["structuredContent"]["error"]
    assert error["code"] == "ERR_TARGET_REQUIRED"
    assert "expected" in error
    assert "example" in error
    text = payload["result"]["content"][0]["text"]
    assert "@HINT expected=" in text


def test_read_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    """legacy repo key fallback이 발생하면 meta.warnings를 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    target_file.write_text("print('a')\n", encoding="utf-8")
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    server = McpServer(db_path=db_path)
    scan_payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6041,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert scan_payload["result"]["isError"] is False

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6042,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": "repo",
                    "mode": "file",
                    "target": "main.py",
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    meta = payload["result"]["structuredContent"]["meta"]
    assert isinstance(meta.get("warnings"), list)
    assert meta["warnings"][0]["code"] == "WARN_REPO_LEGACY_KEY_FALLBACK"


def test_read_symbol_uses_l3_layer_snapshot_when_lsp_symbols_empty(tmp_path: Path) -> None:
    """read(symbol)은 lsp_symbols가 비어도 L3 레이어 스냅샷으로 응답해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    target_file = repo_path / "main.py"
    source = "def alpha_fn():\n    return 1\n"
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    target_file.write_text(source, encoding="utf-8")
    repo_root = str(repo_path.resolve())
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES('repo', 'repo', :repo_root, :repo_root, '2026-02-23T00:00:00Z', 1)
            ON CONFLICT(repo_id) DO UPDATE SET
                repo_label = excluded.repo_label,
                repo_root = excluded.repo_root,
                workspace_root = excluded.workspace_root,
                updated_at = excluded.updated_at,
                is_active = 1
            """,
            {"repo_root": repo_root},
        )
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, 'main.py', :abs_path, 'repo',
                1, :size_bytes, :content_hash, 0, '2026-02-23T00:00:00Z', '2026-02-23T00:00:00Z', 'READY'
            )
            """,
            {
                "repo_root": repo_root,
                "abs_path": str(target_file.resolve()),
                "size_bytes": len(source.encode("utf-8")),
                "content_hash": source_hash,
            },
        )
        conn.commit()
    tool_layer_repo = ToolDataLayerRepository(db_path)
    tool_layer_repo.upsert_l3_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="main.py",
        content_hash=source_hash,
        symbols=[
            {
                "name": "alpha_fn",
                "kind": "function",
                "line": 1,
                "end_line": 2,
                "symbol_key": "alpha_fn@main.py",
                "parent_symbol_key": None,
                "depth": 0,
                "container_name": None,
            }
        ],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at="2026-02-23T00:00:00Z",
    )
    tool_layer_repo.upsert_l4_normalized_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="main.py",
        content_hash=source_hash,
        normalized={"outline": ["alpha_fn"]},
        confidence=0.95,
        ambiguity=0.1,
        coverage=0.9,
        updated_at="2026-02-23T00:00:00Z",
    )
    tool_layer_repo.upsert_l5_semantics(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="main.py",
        content_hash=source_hash,
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 1},
        updated_at="2026-02-23T00:00:00Z",
    )
    server = McpServer(db_path=db_path)
    scan_payload = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6051,
            "method": "tools/call",
            "params": {
                "name": "scan_once",
                "arguments": {"repo": repo_root, "options": {"structured": 1}},
            },
        }
    ).to_dict()
    assert scan_payload["result"]["isError"] is False

    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6052,
            "method": "tools/call",
            "params": {
                "name": "read",
                "arguments": {
                    "repo": repo_root,
                    "mode": "symbol",
                    "target": "alpha_fn",
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()
    assert response["result"]["isError"] is False
    items = response["result"]["structuredContent"]["items"]
    assert len(items) >= 1
    assert items[0]["name"] == "alpha_fn"
    assert isinstance(items[0]["l4"], dict)
    assert items[0]["l4"]["normalized"]["outline"] == ["alpha_fn"]
    assert isinstance(items[0]["l5"], list)
    assert items[0]["l5"][0]["reason_code"] == "L5_REASON_UNRESOLVED_SYMBOL"
