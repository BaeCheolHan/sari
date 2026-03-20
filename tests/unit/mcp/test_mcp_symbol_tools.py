"""MCP 심볼/호출자 도구(search_symbol/get_callers)를 검증한다."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sari.core.models import SymbolSearchItemDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.mcp.server import McpServer
from sari.mcp.tools.symbol_graph_tools import GetImplementationsTool
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


def test_mcp_get_callers_falls_back_to_python_semantic_edges(tmp_path: Path) -> None:
    """LSP relation이 비어도 persisted semantic edge를 호출자 결과로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO python_semantic_call_edges(
                repo_id, repo_root, scope_repo_root, relative_path, content_hash,
                from_symbol, to_symbol, line, evidence_type, confidence, created_at
            )
            VALUES(
                :repo_id, :repo_root, :scope_repo_root, :relative_path, :content_hash,
                :from_symbol, :to_symbol, :line, :evidence_type, :confidence, :created_at
            )
            """,
            {
                "repo_id": "repo-a",
                "repo_root": str(repo_dir.resolve()),
                "scope_repo_root": str(repo_dir.resolve()),
                "relative_path": "src/http/routes.py",
                "content_hash": "h-sem",
                "from_symbol": "build_http_routes",
                "to_symbol": "status_endpoint",
                "line": 47,
                "evidence_type": "python_route_registration",
                "confidence": 0.9,
                "created_at": "2026-03-20T00:00:00+00:00",
            },
        )
        conn.commit()

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 331,
            "method": "tools/call",
            "params": {
                "name": "get_callers",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
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
    assert items[0]["from_symbol"] == "build_http_routes"
    assert items[0]["to_symbol"] == "status_endpoint"
    assert items[0]["evidence_type"] == "python_route_registration"


def test_mcp_call_graph_uses_python_semantic_callers_when_lsp_relations_missing(tmp_path: Path) -> None:
    """call_graph도 semantic caller edge를 callers에 반영해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO python_semantic_call_edges(
                repo_id, repo_root, scope_repo_root, relative_path, content_hash,
                from_symbol, to_symbol, line, evidence_type, confidence, created_at
            )
            VALUES(
                :repo_id, :repo_root, :scope_repo_root, :relative_path, :content_hash,
                :from_symbol, :to_symbol, :line, :evidence_type, :confidence, :created_at
            )
            """,
            {
                "repo_id": "repo-a",
                "repo_root": str(repo_dir.resolve()),
                "scope_repo_root": str(repo_dir.resolve()),
                "relative_path": "src/http/routes.py",
                "content_hash": "h-sem",
                "from_symbol": "build_http_routes",
                "to_symbol": "status_endpoint",
                "line": 47,
                "evidence_type": "python_route_registration",
                "confidence": 0.9,
                "created_at": "2026-03-20T00:00:00+00:00",
            },
        )
        conn.commit()

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 332,
            "method": "tools/call",
            "params": {
                "name": "call_graph",
                "arguments": {
                    "repo": str(repo_dir.resolve()),
                    "symbol": "status_endpoint",
                    "limit": 10,
                    "options": {"structured": 1},
                },
            },
        }
    )
    payload = response.to_dict()

    assert payload["result"]["isError"] is False
    graph = payload["result"]["structuredContent"]["items"][0]
    assert graph["caller_count"] == 1
    assert graph["callers"][0]["from_symbol"] == "build_http_routes"
    assert graph["callers"][0]["evidence_type"] == "python_route_registration"
    assert graph["callers"][0]["confidence"] == 0.9
    assert graph["semantic_callers_used"] is True
    assert graph["caller_evidence_types"] == ["python_route_registration"]
    assert graph["max_caller_confidence"] == 0.9
    warnings = payload["result"]["structuredContent"]["meta"]["warnings"]
    warning_codes = {warning["code"] for warning in warnings}
    assert "WARN_CALL_GRAPH_RELATIONS_NOT_READY" not in warning_codes


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


def test_mcp_get_implementations_falls_back_to_structural_protocol_match(tmp_path: Path) -> None:
    """직접 상속이 없어도 Protocol 메서드 집합을 만족하면 구현체로 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None: ...\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "src" / "service.py").write_text(
        "class FileCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 37,
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
    assert items[0]["evidence_type"] == "python_structural_protocol_match"


def test_mcp_get_implementations_structural_protocol_match_rejects_partial_match(tmp_path: Path) -> None:
    """Protocol 메서드를 일부만 구현한 클래스는 구조적 구현체로 취급하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None: ...\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "src" / "service.py").write_text(
        "class PartialCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 38,
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


def test_mcp_get_implementations_structural_protocol_match_excludes_venv_and_site_packages(tmp_path: Path) -> None:
    """Production 기본 조회에서는 repo 내부 venv/site-packages 후보를 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / ".venv-local-test" / "lib" / "python3.13" / "site-packages" / "sari" / "services").mkdir(parents=True)
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None: ...\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "src" / "service.py").write_text(
        "class FileCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )
    (
        repo_dir
        / ".venv-local-test"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "sari"
        / "services"
        / "file_collection_service.py"
    ).write_text(
        "class FileCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 39,
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


def test_mcp_get_implementations_structural_protocol_match_excludes_build_artifacts(tmp_path: Path) -> None:
    """Production 기본 조회에서는 build 산출물 후보를 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "build" / "lib" / "sari" / "services").mkdir(parents=True)
    (repo_dir / "src" / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class CollectionScanPort(Protocol):\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None: ...\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None: ...\n",
        encoding="utf-8",
    )
    (repo_dir / "src" / "service.py").write_text(
        "class FileCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )
    (repo_dir / "build" / "lib" / "sari" / "services" / "service.py").write_text(
        "class FileCollectionService:\n"
        "    def scan_once(self, repo_root: str, *, trigger: str = 'manual') -> None:\n"
        "        return None\n\n"
        "    def index_file(self, repo_root: str, relative_path: str) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    server = McpServer(db_path=db_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 40,
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


def test_get_implementations_filters_build_artifact_rows_from_repository(tmp_path: Path) -> None:
    """DB 구현체 후보에서도 build 산출물 경로는 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))
    _upsert_repo_identity(db_path, repo_id="repo-a", repo_root=str(repo_dir.resolve()), repo_label="repo-a")

    tool = GetImplementationsTool(
        workspace_repo=WorkspaceRepository(db_path),
        lsp_repo=SimpleNamespace(
            find_implementations=lambda **kwargs: [
                SymbolSearchItemDTO(
                    repo=str(repo_dir.resolve()),
                    relative_path="build/lib/sari/services/collection/service.py",
                    name="FileCollectionService",
                    kind="Class",
                    line=73,
                    end_line=400,
                    content_hash="h-build",
                    symbol_key="build/lib/sari/services/collection/service.py::FileCollectionService@73",
                ),
                SymbolSearchItemDTO(
                    repo=str(repo_dir.resolve()),
                    relative_path="src/sari/services/collection/service.py",
                    name="FileCollectionService",
                    kind="Class",
                    line=73,
                    end_line=400,
                    content_hash="h-src",
                    symbol_key="src/sari/services/collection/service.py::FileCollectionService@73",
                ),
            ]
        ),
    )
    response = tool.call({"repo": str(repo_dir.resolve()), "symbol": "CollectionScanPort", "limit": 20})

    assert response["isError"] is False
    items = response["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["relative_path"] == "src/sari/services/collection/service.py"
