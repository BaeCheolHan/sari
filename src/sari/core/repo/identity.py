"""repo_id 계산 및 repo 식별 메타 유틸을 제공한다."""

from __future__ import annotations

import hashlib
from pathlib import Path


def resolve_workspace_root(repo_root: str, workspace_paths: list[str]) -> str | None:
    """repo_root를 포함하는 가장 긴 workspace 루트를 반환한다."""
    normalized_repo = Path(repo_root).expanduser().resolve()
    matched: list[Path] = []
    for raw_workspace in workspace_paths:
        stripped = str(raw_workspace).strip()
        if stripped == "":
            continue
        workspace_path = Path(stripped).expanduser().resolve()
        if not workspace_path.exists() or not workspace_path.is_dir():
            continue
        try:
            normalized_repo.relative_to(workspace_path)
            matched.append(workspace_path)
        except ValueError:
            continue
    if len(matched) == 0:
        return None
    matched.sort(key=lambda item: len(item.parts), reverse=True)
    return str(matched[0])


def compute_repo_id(repo_label: str, workspace_root: str | None) -> str:
    """workspace+repo_label 조합으로 안정적인 repo_id를 계산한다."""
    scope = workspace_root if workspace_root is not None else "-"
    digest = hashlib.sha1(f"{scope}::{repo_label}".encode("utf-8")).hexdigest()
    return f"r_{digest[:20]}"
