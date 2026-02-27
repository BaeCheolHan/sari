"""CollectionRepoSupport exclude glob 매칭 회귀를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectionPolicyDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.repo_support import CollectionRepoSupport


def _support(tmp_path: Path) -> CollectionRepoSupport:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    policy = CollectionPolicyDTO(
        include_ext=(".java",),
        exclude_globs=("**/bin/**", "**/build/**"),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )
    return CollectionRepoSupport(
        workspace_repo=WorkspaceRepository(db_path),
        policy=policy,
        policy_repo=None,
        lsp_backend=object(),
        repo_registry_repo=None,
        lsp_prewarm_min_language_files=1,
        lsp_prewarm_top_language_count=3,
    )


def test_is_collectible_excludes_top_level_bin_and_build(tmp_path: Path) -> None:
    """top-level bin/build 경로는 기본 exclude glob으로 제외되어야 한다."""
    repo_root = tmp_path / "repo-a"
    (repo_root / "bin").mkdir(parents=True)
    (repo_root / "build").mkdir(parents=True)
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "bin" / "A.java").write_text("class A {}", encoding="utf-8")
    (repo_root / "build" / "B.java").write_text("class B {}", encoding="utf-8")
    (repo_root / "src" / "C.java").write_text("class C {}", encoding="utf-8")

    support = _support(tmp_path)
    gitignore_spec = support.load_gitignore_spec(repo_root)

    assert support.is_collectible(repo_root / "src" / "C.java", repo_root, gitignore_spec)
    assert not support.is_collectible(repo_root / "bin" / "A.java", repo_root, gitignore_spec)
    assert not support.is_collectible(repo_root / "build" / "B.java", repo_root, gitignore_spec)

