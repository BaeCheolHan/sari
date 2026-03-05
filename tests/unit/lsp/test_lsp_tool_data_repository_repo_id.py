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
