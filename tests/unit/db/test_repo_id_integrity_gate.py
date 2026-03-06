"""repo_id 정합성 게이트(init_schema)를 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.core.exceptions import ValidationError
from sari.db.schema import connect, init_schema


def test_init_schema_backfills_empty_repo_id_from_repositories(tmp_path: Path) -> None:
    """repo_root 정확일치가 있으면 빈 repo_id는 자동 백필되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_root = str((tmp_path / "repo-a").resolve())
    (tmp_path / "repo-a").mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES('r_repo_a', 'repo-a', :repo_root, :workspace_root, '2026-03-05T00:00:00Z', 1)
            """,
            {"repo_root": repo_root, "workspace_root": repo_root},
        )
        conn.execute(
            """
            INSERT INTO lsp_symbols(
                repo_id, repo_root, scope_repo_root, relative_path, content_hash,
                name, kind, line, end_line, symbol_key, parent_symbol_key, depth, container_name, created_at
            ) VALUES(
                '', :repo_root, :repo_root, 'main.py', 'h1',
                'foo', 'function', 1, 2, 'foo@main.py', NULL, 0, NULL, '2026-03-05T00:00:00Z'
            )
            """,
            {"repo_root": repo_root},
        )
        conn.commit()

    init_schema(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT repo_id
            FROM lsp_symbols
            WHERE repo_root = :repo_root AND relative_path = 'main.py'
            """,
            {"repo_root": repo_root},
        ).fetchone()
    assert row is not None
    assert str(row["repo_id"]) == "r_repo_a"


def test_init_schema_blocks_startup_when_repo_id_remains_unresolved(tmp_path: Path) -> None:
    """백필 후에도 빈/불일치 repo_id가 남으면 init_schema는 실패해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_index_changes(
                change_type, status, repo_id, repo_root, scope_repo_root, relative_path,
                absolute_path, content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
            ) VALUES(
                'UPSERT', 'PENDING', '', '/not-registered/repo', '/not-registered/repo', 'a.py',
                '/not-registered/repo/a.py', 'h1', 1, 10, 'test', NULL, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """
        )
        conn.commit()

    with pytest.raises(ValidationError) as captured:
        init_schema(db_path)
    assert captured.value.context.code == "ERR_REPO_ID_INTEGRITY"
    assert "candidate_index_changes=1" in captured.value.context.message


def test_init_schema_rewrites_mismatched_repo_id_when_repo_root_matches_registry(tmp_path: Path) -> None:
    """repo_root가 등록돼 있으면 잘못된 repo_id도 SSOT 값으로 교정해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_root = str((tmp_path / "repo-a").resolve())
    (tmp_path / "repo-a").mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES('r_repo_a', 'repo-a', :repo_root, :workspace_root, '2026-03-05T00:00:00Z', 1)
            """,
            {"repo_root": repo_root, "workspace_root": repo_root},
        )
        conn.execute(
            """
            INSERT INTO file_enrich_queue(
                job_id, repo_id, repo_root, scope_repo_root, relative_path, content_hash, content_raw, content_encoding,
                priority, enqueue_source, status, attempt_count, last_error, defer_reason,
                scope_level, scope_root, scope_attempts, next_retry_at, created_at, updated_at
            ) VALUES(
                'j1', 'r_wrong', :repo_root, :repo_root, 'main.py', 'h1', '', 'utf-8',
                30, 'scan', 'PENDING', 0, NULL, NULL,
                NULL, NULL, 0, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """,
            {"repo_root": repo_root},
        )
        conn.commit()

    init_schema(db_path)

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT repo_id
            FROM file_enrich_queue
            WHERE repo_root = :repo_root AND relative_path = 'main.py'
            """,
            {"repo_root": repo_root},
        ).fetchone()
    assert row is not None
    assert str(row["repo_id"]) == "r_repo_a"
