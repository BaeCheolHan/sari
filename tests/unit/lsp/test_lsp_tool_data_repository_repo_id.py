"""LSP tool data 저장 시 repo_id 유지 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import LspExtractPersistDTO
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.schema import connect, init_schema


def test_replace_file_data_many_persists_repo_id_for_symbols_and_relations(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 20}],
                relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 11}],
                created_at="2026-03-05T00:00:00+00:00",
            )
        ]
    )
    with connect(db_path) as conn:
        symbol_row = conn.execute(
            """
            SELECT repo_id
            FROM lsp_symbols
            WHERE repo_root = '/repo' AND relative_path = 'src/main.py'
            LIMIT 1
            """
        ).fetchone()
        relation_row = conn.execute(
            """
            SELECT repo_id
            FROM lsp_call_relations
            WHERE repo_root = '/repo' AND relative_path = 'src/main.py'
            LIMIT 1
            """
        ).fetchone()
    assert symbol_row is not None
    assert relation_row is not None
    assert str(symbol_row["repo_id"]) == "r_repo"
    assert str(relation_row["repo_id"]) == "r_repo"


def test_replace_file_data_many_persists_relation_caller_relative_path(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/service.py",
                content_hash="h1",
                symbols=[{"name": "Service", "kind": "Class", "line": 1, "end_line": 10}],
                relations=[
                    {
                        "from_symbol": "Controller.handle",
                        "to_symbol": "Service.call",
                        "line": 22,
                        "caller_relative_path": "src/controller.py",
                    }
                ],
                created_at="2026-03-23T00:00:00+00:00",
            )
        ]
    )
    with connect(db_path) as conn:
        relation_row = conn.execute(
            """
            SELECT relative_path, caller_relative_path
            FROM lsp_call_relations
            WHERE repo_root = '/repo' AND relative_path = 'src/service.py'
            LIMIT 1
            """
        ).fetchone()
    assert relation_row is not None
    assert str(relation_row["relative_path"]) == "src/service.py"
    assert str(relation_row["caller_relative_path"]) == "src/controller.py"



def test_replace_file_data_many_preserves_same_hash_relations_when_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[{"name": "OldService", "kind": "Class", "line": 10, "end_line": 20}],
                relations=[{"from_symbol": "A", "to_symbol": "B", "line": 11}],
                created_at="2026-03-05T00:00:00+00:00",
            )
        ]
    )
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[{"name": "NewService", "kind": "Class", "line": 30, "end_line": 40}],
                relations=[],
                created_at="2026-03-05T00:01:00+00:00",
                preserve_relations=True,
            )
        ]
    )
    with connect(db_path) as conn:
        symbol_names = [
            str(row["name"]) for row in conn.execute(
                "SELECT name FROM lsp_symbols WHERE repo_root = '/repo' AND relative_path = 'src/main.py' ORDER BY name"
            ).fetchall()
        ]
        relation_row = conn.execute(
            "SELECT from_symbol, to_symbol, content_hash FROM lsp_call_relations WHERE repo_root = '/repo' AND relative_path = 'src/main.py' LIMIT 1"
        ).fetchone()
    assert symbol_names == ["OldService"]
    assert relation_row is not None
    assert str(relation_row["from_symbol"]) == "A"
    assert str(relation_row["content_hash"]) == "h1"


def test_replace_file_data_many_preserve_same_hash_keeps_richer_symbol_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[
                    {"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40},
                    {"name": "AuthService.login", "kind": "Function", "line": 15, "end_line": 20},
                ],
                relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 16}],
                created_at="2026-03-05T00:00:00+00:00",
            )
        ]
    )
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40}],
                relations=[],
                created_at="2026-03-05T00:01:00+00:00",
                preserve_relations=True,
            )
        ]
    )
    with connect(db_path) as conn:
        symbol_names = [
            str(row["name"]) for row in conn.execute(
                "SELECT name FROM lsp_symbols WHERE repo_root = '/repo' AND relative_path = 'src/main.py' ORDER BY line, name"
            ).fetchall()
        ]
        relation_names = [
            tuple(str(value) for value in row)
            for row in conn.execute(
                "SELECT from_symbol, to_symbol FROM lsp_call_relations WHERE repo_root = '/repo' AND relative_path = 'src/main.py'"
            ).fetchall()
        ]
    assert symbol_names == ["AuthService", "AuthService.login"]
    assert relation_names == [("AuthController.login", "AuthService.login")]


def test_replace_file_data_many_clears_old_hash_relations_even_when_preserve_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="old-hash",
                symbols=[{"name": "OldService", "kind": "Class", "line": 10, "end_line": 20}],
                relations=[{"from_symbol": "A", "to_symbol": "B", "line": 11}],
                created_at="2026-03-05T00:00:00+00:00",
            )
        ]
    )
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="new-hash",
                symbols=[{"name": "NewService", "kind": "Class", "line": 30, "end_line": 40}],
                relations=[],
                created_at="2026-03-05T00:01:00+00:00",
                preserve_relations=True,
            )
        ]
    )
    with connect(db_path) as conn:
        relation_count = int(conn.execute(
            "SELECT COUNT(*) FROM lsp_call_relations WHERE repo_root = '/repo' AND relative_path = 'src/main.py'"
        ).fetchone()[0])
    assert relation_count == 0


def test_replace_file_data_many_persists_python_semantic_call_edges_from_content_text(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/http/routes.py",
                content_hash="h1",
                symbols=[{"name": "status_endpoint", "kind": "Function", "line": 3, "end_line": 4}],
                relations=[],
                created_at="2026-03-20T00:00:00+00:00",
                content_text=(
                    "from starlette.routing import Route\n\n"
                    "def status_endpoint(request):\n"
                    "    return {'ok': True}\n\n"
                    "def build_http_routes():\n"
                    "    return [Route('/status', status_endpoint)]\n"
                ),
            )
        ]
    )

    rows = repo.find_python_semantic_callers(repo_root="/repo", symbol_name="status_endpoint", limit=10)

    assert len(rows) == 1
    assert rows[0]["from_symbol"] == "build_http_routes"
    assert rows[0]["to_symbol"] == "status_endpoint"
    assert rows[0]["evidence_type"] == "python_route_registration"


def test_replace_file_data_many_ignores_relation_unique_conflicts(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/target.py",
                content_hash="h1",
                symbols=[{"name": "Target", "kind": "Function", "line": 1, "end_line": 1}],
                relations=[
                    {
                        "from_symbol": "shared_name",
                        "to_symbol": "Target",
                        "line": 10,
                        "caller_relative_path": "src/caller_a.py",
                    },
                    {
                        "from_symbol": "shared_name",
                        "to_symbol": "Target",
                        "line": 10,
                        "caller_relative_path": "src/caller_b.py",
                    },
                ],
                created_at="2026-03-23T00:00:00+00:00",
            )
        ]
    )

    with connect(db_path) as conn:
        persisted = conn.execute(
            "SELECT COUNT(*) FROM lsp_call_relations WHERE repo_root='/repo' AND relative_path='src/target.py'"
        ).fetchone()[0]
    assert int(persisted) >= 1


def test_replace_file_data_many_persists_relation_symbol_keys_from_symbol_table(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_file_data_many(
        [
            LspExtractPersistDTO(
                repo_id="r_repo",
                repo_root="/repo",
                relative_path="src/main.py",
                content_hash="h1",
                symbols=[
                    {
                        "name": "AuthController.login",
                        "kind": "Function",
                        "line": 3,
                        "end_line": 8,
                        "symbol_key": "py:/repo/src/main.py#AuthController.login",
                    },
                    {
                        "name": "AuthService.login",
                        "kind": "Function",
                        "line": 12,
                        "end_line": 20,
                        "symbol_key": "py:/repo/src/main.py#AuthService.login",
                    },
                ],
                relations=[
                    {
                        "from_symbol": "AuthController.login",
                        "to_symbol": "AuthService.login",
                        "line": 13,
                    }
                ],
                created_at="2026-03-24T00:00:00+00:00",
            )
        ]
    )
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT from_symbol_key, to_symbol_key
            FROM lsp_call_relations
            WHERE repo_root='/repo' AND relative_path='src/main.py'
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert str(row["from_symbol_key"]) == "py:/repo/src/main.py#AuthController.login"
    assert str(row["to_symbol_key"]) == "py:/repo/src/main.py#AuthService.login"


def test_find_callers_and_callees_support_symbol_key_lookup(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)
    repo.replace_relations(
        repo_root="/repo",
        relative_path="src/main.py",
        content_hash="h1",
        relations=[
            {
                "from_symbol": "AuthController.login",
                "to_symbol": "AuthService.login",
                "from_symbol_key": "py:/repo/src/main.py#AuthController.login",
                "to_symbol_key": "py:/repo/src/main.py#AuthService.login",
                "line": 13,
            }
        ],
        created_at="2026-03-24T00:00:00+00:00",
    )

    callers = repo.find_callers(
        repo_root="/repo",
        symbol_name="py:/repo/src/main.py#AuthService.login",
        limit=20,
    )
    callees = repo.find_callees(
        repo_root="/repo",
        symbol_name="py:/repo/src/main.py#AuthController.login",
        limit=20,
    )

    assert len(callers) == 1
    assert callers[0].from_symbol == "AuthController.login"
    assert len(callees) == 1
    assert callees[0].to_symbol == "AuthService.login"
