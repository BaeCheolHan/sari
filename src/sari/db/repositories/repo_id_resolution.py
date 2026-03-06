"""DB 레이어에서 repo_root 기준 repo_id를 SSOT 규칙으로 해석한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.repo.identity import compute_repo_id, resolve_workspace_root
from sari.core.repo.resolver import resolve_repo_key
from sari.db.row_mapper import row_optional_str


def resolve_repo_id_for_repo_root(conn, repo_root: str) -> str:
    """repo_root에 대응하는 repo_id를 repositories/workspaces 기준으로 해석한다."""
    normalized_root = str(Path(repo_root).expanduser().resolve(strict=False))
    row = conn.execute(
        """
        SELECT repo_id
        FROM repositories
        WHERE repo_root = :repo_root
          AND is_active = 1
        LIMIT 1
        """,
        {"repo_root": normalized_root},
    ).fetchone()
    if row is not None:
        repo_id = row_optional_str(row, "repo_id")
        if repo_id is not None:
            return repo_id

    workspace_rows = conn.execute(
        """
        SELECT path
        FROM workspaces
        WHERE is_active = 1
        """
    ).fetchall()
    workspace_paths = [
        path_value
        for row in workspace_rows
        for path_value in [row_optional_str(row, "path")]
        if path_value is not None
    ]
    workspace_root = resolve_workspace_root(repo_root=normalized_root, workspace_paths=workspace_paths)
    repo_label = resolve_repo_key(repo_root=normalized_root, workspace_paths=workspace_paths)
    return compute_repo_id(repo_label=repo_label, workspace_root=workspace_root)
