"""LSP 심볼 계층 필드 저장을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.schema import connect, init_schema


def test_replace_symbols_persists_hierarchy_columns(tmp_path: Path) -> None:
    """symbol_key/parent_symbol_key/depth/container_name 이 저장되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = LspToolDataRepository(db_path)

    repo.replace_symbols(
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        symbols=[
            {
                "name": "Alpha",
                "kind": "class",
                "line": 1,
                "end_line": 10,
                "symbol_key": "sym-root",
                "parent_symbol_key": None,
                "depth": 0,
                "container_name": None,
            },
            {
                "name": "run",
                "kind": "method",
                "line": 3,
                "end_line": 8,
                "symbol_key": "sym-child",
                "parent_symbol_key": "sym-root",
                "depth": 1,
                "container_name": "Alpha",
            },
        ],
        created_at="2026-02-17T00:00:00+00:00",
    )

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol_key, parent_symbol_key, depth, container_name
            FROM lsp_symbols
            WHERE repo_root = :repo_root
              AND relative_path = :relative_path
            ORDER BY line ASC
            """,
            {"repo_root": "/repo", "relative_path": "a.py"},
        ).fetchall()

    assert len(rows) == 2
    assert rows[0]["symbol_key"] == "sym-root"
    assert rows[0]["parent_symbol_key"] is None
    assert int(rows[0]["depth"]) == 0
    assert rows[1]["symbol_key"] == "sym-child"
    assert rows[1]["parent_symbol_key"] == "sym-root"
    assert int(rows[1]["depth"]) == 1
    assert rows[1]["container_name"] == "Alpha"
