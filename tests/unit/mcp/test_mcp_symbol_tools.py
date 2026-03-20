"""MCP 심볼/호출자 도구(search_symbol/get_callers)를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.mcp.server import McpServer
from sari.services.workspace.service import WorkspaceService


def _upsert_repo_identity(db_path: Path, *, repo_id: str, repo_root: str, repo_label: str) -> None:
    """repo_id 정합성 게이트를 통과하기 위한 repositories 행을 준비한다."""
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES(:repo_id, :repo_label, :repo_root, :workspace_root, '2026-03-05T00:00:00Z', 1)
            ON CONFLICT(repo_id) DO UPDATE SET
                repo_label = excluded.repo_label,
                repo_root = excluded.repo_root,
                workspace_root = excluded.workspace_root,
                updated_at = excluded.updated_at,
                is_active = 1
            """,
            {
                "repo_id": repo_id,
                "repo_label": repo_label,
                "repo_root": repo_root,
                "workspace_root": repo_root,
            },
        )
        conn.commit()


def test_mcp_tools_list_includes_symbol_tools(tmp_path: Path) -> None:
    """tools/list 응답은 search_symbol/get_callers를 포함해야 한다."""
    server = McpServer(db_path=tmp_path / "state.db")

    list_response = server.handle_request({"jsonrpc": "2.0", "id": 31, "method": "tools/list"})
    payload = list_response.to_dict()
    names = {tool["name"] for tool in payload["result"]["tools"]}

    assert "search_symbol" in names
    assert "get_callers" in names


def test_mcp_search_symbol_returns_indexed_symbols(tmp_path: Path) -> None:
    """search_symbol은 LSP 심볼 인덱스 결과를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {
                "name": "search_symbol",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "query": "Auth",
                        "limit": 10,
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["name"] == "AuthService"
    assert items[0]["relative_path"] == "src/main.py"


def test_mcp_get_callers_returns_relation_edges(tmp_path: Path) -> None:
    """get_callers는 호출 관계 인덱스 결과를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "symbol": "AuthService.login",
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
    assert items[0]["from_symbol"] == "AuthController.login"
    assert items[0]["to_symbol"] == "AuthService.login"


def test_mcp_get_callers_requires_symbol_or_symbol_id(tmp_path: Path) -> None:
    """get_callers는 symbol 또는 symbol_id 중 하나를 요구해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                    "arguments": {
                        "repo": str(repo_dir.resolve()),
                        "options": {"structured": 1},
                    },
                },
            }
        )
    payload = response.to_dict()

    assert payload["result"]["isError"] is True
    assert payload["result"]["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_SYMBOL_REQUIRED"


def test_mcp_search_symbol_supports_scope_root_repo(tmp_path: Path) -> None:
    """repo를 scope_root로 넘겨도 하위 모듈 심볼을 조회해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = tmp_path / "workspace"
    module_root = scope_root / "mod-a"
    module_root.mkdir(parents=True)
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(scope_root.resolve()))
    _upsert_repo_identity(db_path, repo_id="scope-root", repo_root=str(scope_root.resolve()), repo_label="workspace")
    _upsert_repo_identity(db_path, repo_id="mod-a", repo_root=str(module_root.resolve()), repo_label="mod-a")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(module_root.resolve()),
                "scope_repo_root": str(scope_root.resolve()),
                "absolute_path": str((module_root / "src" / "main.py").resolve()),
            },
        )
        conn.commit()

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(module_root.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        symbols=[{"name": "ScopeAuthService", "kind": "Class", "line": 10, "end_line": 40}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 35,
            "method": "tools/call",
            "params": {
                "name": "search_symbol",
                "arguments": {
                    "repo": str(scope_root.resolve()),
                    "query": "ScopeAuth",
                    "limit": 10,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()
    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["repo"] == str(module_root.resolve())
    assert items[0]["name"] == "ScopeAuthService"


def test_mcp_get_callers_supports_scope_root_repo(tmp_path: Path) -> None:
    """repo를 scope_root로 넘겨도 하위 모듈 callers를 조회해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = tmp_path / "workspace"
    module_root = scope_root / "mod-a"
    module_root.mkdir(parents=True)
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(scope_root.resolve()))
    _upsert_repo_identity(db_path, repo_id="scope-root", repo_root=str(scope_root.resolve()), repo_label="workspace")
    _upsert_repo_identity(db_path, repo_id="mod-a", repo_root=str(module_root.resolve()), repo_label="mod-a")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(module_root.resolve()),
                "scope_repo_root": str(scope_root.resolve()),
                "absolute_path": str((module_root / "src" / "main.py").resolve()),
            },
        )
        conn.commit()

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_relations(
        repo_root=str(module_root.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "ScopeController.login", "to_symbol": "ScopeService.login", "line": 21}],
        created_at="2026-02-16T08:30:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 36,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(scope_root.resolve()),
                    "symbol": "ScopeService.login",
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
    assert items[0]["repo"] == str(module_root.resolve())
    assert items[0]["from_symbol"] == "ScopeController.login"


def test_mcp_get_callers_defaults_to_production_scope_and_exposes_confidence(tmp_path: Path) -> None:
    """기본 get_callers는 테스트 경로를 숨기고 confidence 메타를 노출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-16T08:30:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="tests/test_auth.py",
        content_hash="h2",
        relations=[{"from_symbol": "TestAuth.login", "to_symbol": "AuthService.login", "line": 31}],
        created_at="2026-02-16T08:31:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 37,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "AuthService.login",
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
    assert items[0]["relative_path"] == "src/main.py"
    assert items[0]["confidence"] == 1.0
    assert items[0]["evidence_type"] == "exact_symbol_name"


def test_mcp_call_graph_all_scope_includes_test_edges(tmp_path: Path) -> None:
    """scope=all 이면 call_graph는 테스트 경로 caller도 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-16T08:30:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="tests/test_auth.py",
        content_hash="h2",
        relations=[{"from_symbol": "TestAuth.login", "to_symbol": "AuthService.login", "line": 31}],
        created_at="2026-02-16T08:31:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 38,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "AuthService.login",
                    "scope": "all",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["caller_count"] == 2
    assert item["confidence"] == 1.0
    assert item["evidence_type"] == "exact_symbol_name"


def test_mcp_get_callers_resolves_symbol_id_via_symbol_key(tmp_path: Path) -> None:
    """symbol_id는 symbol_key를 우선 해석해 canonical name relation을 조회해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/auth.py",
        content_hash="h1",
        symbols=[
            {
                "name": "AuthService.login",
                "kind": "Function",
                "line": 10,
                "end_line": 12,
                "symbol_key": "src/auth.py::AuthService.login@10",
            }
        ],
        created_at="2026-03-20T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h2",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-03-20T00:00:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 39,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol_id": "src/auth.py::AuthService.login@10",
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
    assert items[0]["to_symbol"] == "AuthService.login"


def test_mcp_call_graph_health_exposes_scope_quality_breakdown(tmp_path: Path) -> None:
    """call_graph_health는 production/test 분리 품질 지표를 노출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/auth.py",
        content_hash="h1",
        symbols=[{"name": "AuthService.login", "kind": "Function", "line": 10, "end_line": 12}],
        created_at="2026-03-20T00:00:00+00:00",
    )
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="tests/test_auth.py",
        content_hash="h2",
        symbols=[{"name": "TestAuth.login", "kind": "Function", "line": 20, "end_line": 22}],
        created_at="2026-03-20T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/auth.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 15}],
        created_at="2026-03-20T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="tests/test_auth.py",
        content_hash="h2",
        relations=[{"from_symbol": "TestAuth.login", "to_symbol": "AuthService.login", "line": 25}],
        created_at="2026-03-20T00:00:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 40,
            "method": "tools/call",
            "params": {
                "name": "call_graph_health",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "scope": "all",
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["production_symbol_count"] == 1
    assert item["production_relation_count"] == 1
    assert item["test_symbol_count"] == 1
    assert item["test_relation_count"] == 1
    assert item["cross_file_semantic_relation_count"] == 0


def test_mcp_get_callers_accepts_sid_alias(tmp_path: Path) -> None:
    """get_callers는 sid 별칭도 symbol_key처럼 해석해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/auth.py",
        content_hash="h1",
        symbols=[
            {
                "name": "AuthService.login",
                "kind": "Function",
                "line": 10,
                "end_line": 12,
                "symbol_key": "src/auth.py::AuthService.login@10",
            }
        ],
        created_at="2026-03-20T00:00:00+00:00",
    )
    lsp_repo.replace_relations(
        repo_root=str(repo_dir.resolve()),
        relative_path="src/main.py",
        content_hash="h2",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-03-20T00:00:00+00:00",
    )

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 41,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "sid": "src/auth.py::AuthService.login@10",
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
    assert items[0]["to_symbol"] == "AuthService.login"


def test_mcp_get_implementations_falls_back_to_python_protocol_scan(tmp_path: Path) -> None:
    """get_implementations는 Python Protocol 상속 구현체를 파일 스캔으로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "src" / "service.py").write_text(
        "from src.ports import CollectionScanPort\n\n"
        "class FileCollectionService(CollectionScanPort):\n"
        "    def scan_once(self, repo_root: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {
                "name": "get_implementations",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "CollectionScanPort",
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
    assert items[0]["name"] == "FileCollectionService"
    assert items[0]["relative_path"] == "src/service.py"
    assert items[0]["evidence_type"] == "python_protocol_base"


def test_mcp_get_implementations_protocol_scan_defaults_to_production_scope(tmp_path: Path) -> None:
    """Python fallback 구현체 스캔도 기본적으로 tests 경로를 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "tests").mkdir()
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "tests" / "test_service.py").write_text(
        "from src.ports import CollectionScanPort\n\n"
        "class TestCollectionService(CollectionScanPort):\n"
        "    def scan_once(self, repo_root: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 43,
            "method": "tools/call",
            "params": {
                "name": "get_implementations",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "CollectionScanPort",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    assert payload["result"]["structuredContent"]["items"] == []


def test_mcp_get_callers_falls_back_to_route_registration_scan(tmp_path: Path) -> None:
    """get_callers는 Starlette Route 등록을 semantic caller edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    (repo_dir / "http" / "routes.py").write_text(
        "from starlette.routing import Route\n"
        "from http.meta import status_endpoint\n\n"
        "def build_http_routes():\n"
        "    return [Route('/status', status_endpoint)]\n",
        encoding="utf-8",
    )
    (repo_dir / "http" / "meta.py").write_text(
        "async def status_endpoint(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 44,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
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
    assert items[0]["from_symbol"] == "build_http_routes"
    assert items[0]["evidence_type"] == "python_route_registration"


def test_mcp_call_graph_falls_back_to_mcp_dispatch_scan(tmp_path: Path) -> None:
    """call_graph는 generic handler.call dispatch를 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "mcp").mkdir()
    (repo_dir / "mcp" / "tool_registry.py").write_text(
        "TOOL_REGISTRY = [\n"
        "    {'name': 'get_callers', 'handler_attr': '_get_callers_tool'},\n"
        "]\n",
        encoding="utf-8",
    )
    (repo_dir / "mcp" / "server.py").write_text(
        "from mcp.tool_impl import GetCallersTool\n\n"
        "class McpServer:\n"
        "    def __init__(self):\n"
        "        self._get_callers_tool = GetCallersTool()\n\n"
        "    def handle_request(self, payload):\n"
        "        handler = self._get_callers_tool\n"
        "        return handler.call(payload)\n",
        encoding="utf-8",
    )
    (repo_dir / "mcp" / "tool_impl.py").write_text(
        "class GetCallersTool:\n"
        "    def call(self, arguments):\n"
        "        return arguments\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 45,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "GetCallersTool.call",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["caller_count"] == 1
    assert item["callers"][0]["from_symbol"] == "McpServer.handle_request"
    assert item["callers"][0]["evidence_type"] == "python_mcp_dispatch"


def test_mcp_call_graph_falls_back_to_bound_attribute_dispatch_scan(tmp_path: Path) -> None:
    """call_graph는 __init__ 바인딩된 self._dep.method 호출도 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "app").mkdir()
    (repo_dir / "app" / "tool.py").write_text(
        "class ReadTool:\n"
        "    def call(self, arguments):\n"
        "        return arguments\n",
        encoding="utf-8",
    )
    (repo_dir / "app" / "facade.py").write_text(
        "from app.tool import ReadTool\n\n"
        "class ReadFacadeService:\n"
        "    def __init__(self, read_tool: ReadTool):\n"
        "        self._read_tool = read_tool\n\n"
        "    def read(self, arguments):\n"
        "        return self._read_tool.call(arguments)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 46,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "ReadTool.call",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["caller_count"] == 1
    assert item["callers"][0]["from_symbol"] == "ReadFacadeService.read"
    assert item["callers"][0]["evidence_type"] == "python_bound_attribute_call"


def test_mcp_get_callers_reuses_persisted_python_semantic_edges(tmp_path: Path) -> None:
    """첫 semantic scan 결과는 DB에 저장되어 이후 파일 재스캔 없이 재사용돼야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    route_file = repo_dir / "http" / "routes.py"
    route_file.write_text(
        "from starlette.routing import Route\n"
        "from http.meta import status_endpoint\n\n"
        "def build_http_routes():\n"
        "    return [Route('/status', status_endpoint)]\n",
        encoding="utf-8",
    )
    (repo_dir / "http" / "meta.py").write_text(
        "async def status_endpoint(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")
    server = McpServer(db_path=db_path)

    first = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 47,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()

    assert first["result"]["isError"] is False
    assert len(first["result"]["structuredContent"]["items"]) == 1

    route_file.unlink()
    second = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 48,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()

    assert second["result"]["isError"] is False
    items = second["result"]["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["from_symbol"] == "build_http_routes"
    assert items[0]["evidence_type"] == "python_route_registration"


def test_mcp_call_graph_falls_back_to_registry_dispatch_scan(tmp_path: Path) -> None:
    """call_graph는 dict registry 기반 handler dispatch도 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "mcp").mkdir()
    (repo_dir / "mcp" / "tool_impl.py").write_text(
        "class GetCallersTool:\n"
        "    def call(self, arguments):\n"
        "        return arguments\n",
        encoding="utf-8",
    )
    (repo_dir / "mcp" / "server.py").write_text(
        "from mcp.tool_impl import GetCallersTool\n\n"
        "class McpServer:\n"
        "    def __init__(self):\n"
        "        self._get_callers_tool = GetCallersTool()\n"
        "        self._handlers = {\n"
        "            'get_callers': self._get_callers_tool.call,\n"
        "        }\n\n"
        "    def handle_request(self, payload):\n"
        "        handler = self._handlers[payload['name']]\n"
        "        return handler(payload)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 49,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "GetCallersTool.call",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["caller_count"] == 1
    assert item["callers"][0]["from_symbol"] == "McpServer.handle_request"
    assert item["callers"][0]["evidence_type"] == "python_registry_dispatch"


def test_mcp_get_callers_falls_back_to_route_decorator_scan(tmp_path: Path) -> None:
    """get_callers는 router.get decorator registration도 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    (repo_dir / "http" / "routes.py").write_text(
        "class Router:\n"
        "    def get(self, path):\n"
        "        def decorator(fn):\n"
        "            return fn\n"
        "        return decorator\n\n"
        "router = Router()\n\n"
        "@router.get('/status')\n"
        "async def status_endpoint(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
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
    assert items[0]["from_symbol"] == "router.get"
    assert items[0]["evidence_type"] == "python_route_decorator"


def test_mcp_get_callers_falls_back_to_add_api_route_scan(tmp_path: Path) -> None:
    """get_callers는 router.add_api_route endpoint registration도 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    (repo_dir / "http" / "routes.py").write_text(
        "def status_endpoint(request):\n"
        "    return {'ok': True}\n\n"
        "class Router:\n"
        "    def add_api_route(self, path, endpoint):\n"
        "        return endpoint\n\n"
        "def build_routes(router):\n"
        "    router.add_api_route('/status', status_endpoint)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
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
    assert items[0]["from_symbol"] == "build_routes"
    assert items[0]["evidence_type"] == "python_route_registration"


def test_mcp_call_graph_resolves_literal_key_in_multi_registry_dispatch(tmp_path: Path) -> None:
    """call_graph는 다중 registry에서도 literal key subscript면 정확한 handler target을 골라야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "mcp").mkdir()
    (repo_dir / "mcp" / "tool_impl.py").write_text(
        "class GetCallersTool:\n"
        "    def call(self, arguments):\n"
        "        return arguments\n\n"
        "class ReadTool:\n"
        "    def call(self, arguments):\n"
        "        return arguments\n",
        encoding="utf-8",
    )
    (repo_dir / "mcp" / "server.py").write_text(
        "from mcp.tool_impl import GetCallersTool, ReadTool\n\n"
        "class McpServer:\n"
        "    def __init__(self):\n"
        "        self._get_callers_tool = GetCallersTool()\n"
        "        self._read_tool = ReadTool()\n"
        "        self._handlers = {\n"
        "            'get_callers': self._get_callers_tool.call,\n"
        "            'read': self._read_tool.call,\n"
        "        }\n\n"
        "    def handle_request(self, payload):\n"
        "        return self._handlers['get_callers'](payload)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 52,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "GetCallersTool.call",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    item = payload["result"]["structuredContent"]["items"][0]
    assert item["caller_count"] == 1
    assert item["callers"][0]["from_symbol"] == "McpServer.handle_request"
    assert item["callers"][0]["to_symbol"] == "GetCallersTool.call"
    assert item["callers"][0]["evidence_type"] == "python_registry_dispatch"


def test_mcp_get_callers_falls_back_to_include_router_cross_file_scan(tmp_path: Path) -> None:
    """get_callers는 cross-file include_router composition도 semantic edge로 보강해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    (repo_dir / "http" / "api.py").write_text(
        "class Router:\n"
        "    def get(self, path):\n"
        "        def decorator(fn):\n"
        "            return fn\n"
        "        return decorator\n\n"
        "router = Router()\n\n"
        "@router.get('/status')\n"
        "async def status_endpoint(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (repo_dir / "http" / "app.py").write_text(
        "from http.api import router as api_router\n\n"
        "class App:\n"
        "    def include_router(self, router):\n"
        "        return router\n\n"
        "def mount_routes(app):\n"
        "    app.include_router(api_router)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 53,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    items = payload["result"]["structuredContent"]["items"]
    assert any(item["from_symbol"] == "mount_routes" for item in items)
    assert any(item["evidence_type"] == "python_include_router" for item in items)


def test_mcp_get_callers_reuses_persisted_include_router_cross_file_edges(tmp_path: Path) -> None:
    """include_router cross-file edge도 첫 scan 뒤에는 DB에서 재사용돼야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "http").mkdir()
    (repo_dir / "http" / "api.py").write_text(
        "class Router:\n"
        "    def get(self, path):\n"
        "        def decorator(fn):\n"
        "            return fn\n"
        "        return decorator\n\n"
        "router = Router()\n\n"
        "@router.get('/status')\n"
        "async def status_endpoint(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    app_file = repo_dir / "http" / "app.py"
    app_file.write_text(
        "from http.api import router as api_router\n\n"
        "class App:\n"
        "    def include_router(self, router):\n"
        "        return router\n\n"
        "def mount_routes(app):\n"
        "    app.include_router(api_router)\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")
    server = McpServer(db_path=db_path)

    first = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 54,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()

    assert first["result"]["isError"] is False
    assert any(
        item["from_symbol"] == "mount_routes" and item["evidence_type"] == "python_include_router"
        for item in first["result"]["structuredContent"]["items"]
    )

    app_file.unlink()
    second = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 55,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 20,
                    "options": {"structured": 1},
                },
            },
        }
    ).to_dict()

    assert second["result"]["isError"] is False
    assert any(
        item["from_symbol"] == "mount_routes" and item["evidence_type"] == "python_include_router"
        for item in second["result"]["structuredContent"]["items"]
    )
