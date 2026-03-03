"""Workspace fan-out resolver의 빌드 산출물 제외 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

from sari.core.models import WorkspaceDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.repo_support import WorkspaceFanoutResolver


def test_fanout_resolver_skips_build_artifact_children(tmp_path: Path) -> None:
    """workspace fan-out 후보에서 build/bin 디렉터리는 제외되어야 한다."""
    workspace_root = tmp_path / "corp-api"
    (workspace_root / "src").mkdir(parents=True)
    (workspace_root / "module-a").mkdir(parents=True)
    (workspace_root / "build").mkdir(parents=True)
    (workspace_root / "bin").mkdir(parents=True)

    # 후보 판정용 더미 파일
    (workspace_root / "src" / "A.java").write_text("class A {}", encoding="utf-8")
    (workspace_root / "module-a" / "B.java").write_text("class B {}", encoding="utf-8")
    (workspace_root / "build" / "C.java").write_text("class C {}", encoding="utf-8")
    (workspace_root / "bin" / "D.java").write_text("class D {}", encoding="utf-8")

    # workspace root .gitignore에도 build/bin을 명시
    (workspace_root / ".gitignore").write_text("build\nbin/\n", encoding="utf-8")

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(workspace_root.resolve()),
            name="corp-api",
            indexed_at=None,
            is_active=True,
        )
    )

    resolver = WorkspaceFanoutResolver(
        workspace_repo=workspace_repo,
        load_gitignore_spec=lambda root: PathSpec.from_lines(
            GitWildMatchPattern,
            (root / ".gitignore").read_text(encoding="utf-8").splitlines() if (root / ".gitignore").exists() else [],
        ),
        is_collectible=lambda file_path, repo_root, gitignore_spec: file_path.suffix == ".java"
        and not gitignore_spec.match_file(file_path.relative_to(repo_root).as_posix()),
        build_markers=("pom.xml", "build.gradle", "build.gradle.kts"),
    )

    targets = {str(path) for path in resolver.resolve_targets(workspace_root.resolve())}

    assert str((workspace_root / "src").resolve()) in targets
    assert str((workspace_root / "module-a").resolve()) in targets
    assert str((workspace_root / "build").resolve()) not in targets
    assert str((workspace_root / "bin").resolve()) not in targets


def test_fanout_resolver_returns_empty_when_root_is_explicit_repo(tmp_path: Path) -> None:
    """workspace로 등록되어도 root 자체가 repo면 fan-out을 하지 않아야 한다."""
    workspace_root = tmp_path / "sari"
    workspace_root.mkdir(parents=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='sari'\n", encoding="utf-8")
    (workspace_root / "src").mkdir(parents=True)
    (workspace_root / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (workspace_root / "tests").mkdir(parents=True)
    (workspace_root / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(workspace_root.resolve()),
            name="sari",
            indexed_at=None,
            is_active=True,
        )
    )

    resolver = WorkspaceFanoutResolver(
        workspace_repo=workspace_repo,
        load_gitignore_spec=lambda root: PathSpec.from_lines(
            GitWildMatchPattern,
            (root / ".gitignore").read_text(encoding="utf-8").splitlines() if (root / ".gitignore").exists() else [],
        ),
        is_collectible=lambda file_path, repo_root, gitignore_spec: file_path.suffix == ".py"
        and not gitignore_spec.match_file(file_path.relative_to(repo_root).as_posix()),
        build_markers=("pyproject.toml",),
    )

    targets = resolver.resolve_targets(workspace_root.resolve())
    assert targets == []
