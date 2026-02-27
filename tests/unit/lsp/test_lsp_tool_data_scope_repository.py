"""LSP tool data 저장소의 scope_root 조회 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.schema import connect, init_schema


def test_lsp_tool_data_repository_search_symbols_supports_scope_root(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', '/workspace/mod-a/src/main.py', 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root},
        )
        conn.commit()

    repo.replace_symbols(
        repo_root=module_root,
        relative_path="src/main.py",
        content_hash="h1",
        symbols=[{"name": "AuthService", "kind": "Class", "line": 10, "end_line": 40}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    rows = repo.search_symbols(repo_root=scope_root, query="Auth", limit=10)
    assert len(rows) == 1
    assert rows[0].repo == module_root
    assert rows[0].name == "AuthService"


def test_lsp_tool_data_repository_callers_and_health_support_scope_root(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_root = "/workspace/mod-a"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', '/workspace/mod-a/src/main.py', 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {"repo_root": module_root, "scope_repo_root": scope_root},
        )
        conn.commit()

    repo.replace_relations(
        repo_root=module_root,
        relative_path="src/main.py",
        content_hash="h1",
        relations=[{"from_symbol": "AuthController.login", "to_symbol": "AuthService.login", "line": 21}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    callers = repo.find_callers(repo_root=scope_root, symbol_name="AuthService.login", limit=20)
    assert len(callers) == 1
    assert callers[0].repo == module_root
    assert callers[0].from_symbol == "AuthController.login"

    assert repo.count_distinct_callers(repo_root=scope_root, symbol_name="AuthService.login") == 1
    health = repo.get_repo_call_graph_health(repo_root=scope_root)
    assert health["relation_count"] >= 1


def test_lsp_tool_data_repository_count_distinct_callers_counts_per_repo_path_pair(tmp_path: Path) -> None:
    """scope 집계 시 동일 relative_path라도 module repo가 다르면 caller를 별도로 집계해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    scope_root = "/workspace"
    module_a = "/workspace/mod-a"
    module_b = "/workspace/mod-b"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_a,
                "scope_repo_root": scope_root,
                "absolute_path": "/workspace/mod-a/src/main.py",
                "content_hash": "ha",
            },
        )
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'src/main.py', :absolute_path, 'mod',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_b,
                "scope_repo_root": scope_root,
                "absolute_path": "/workspace/mod-b/src/main.py",
                "content_hash": "hb",
            },
        )
        conn.commit()

    repo.replace_relations(
        repo_root=module_a,
        relative_path="src/main.py",
        content_hash="ha",
        relations=[{"from_symbol": "A.call", "to_symbol": "Common.target", "line": 10}],
        created_at="2026-02-25T00:00:00+00:00",
    )
    repo.replace_relations(
        repo_root=module_b,
        relative_path="src/main.py",
        content_hash="hb",
        relations=[{"from_symbol": "B.call", "to_symbol": "Common.target", "line": 20}],
        created_at="2026-02-25T00:00:00+00:00",
    )

    assert repo.count_distinct_callers(repo_root=scope_root, symbol_name="Common.target") == 2
