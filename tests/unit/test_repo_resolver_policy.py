"""repo 하이브리드 판별 정책을 검증한다."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sari.core.exceptions import ValidationError
from sari.core.repo.resolver import resolve_repo_root


def test_repo_resolver_prefers_git_root_when_registered(tmp_path: Path) -> None:
    """git 루트가 등록 워크스페이스와 일치하면 git 기준을 사용해야 한다."""
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "src" / "module"
    nested.mkdir(parents=True)

    resolved = resolve_repo_root(str(nested), [str(repo_root)])

    assert resolved == str(repo_root.resolve())


def test_repo_resolver_uses_build_marker_when_git_missing(tmp_path: Path) -> None:
    """git이 없으면 빌드 마커 경로를 기준으로 해석해야 한다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    module_root = workspace_root / "services" / "alpha"
    module_root.mkdir(parents=True)
    (module_root / "package.json").write_text("{\"name\":\"alpha\"}\n", encoding="utf-8")
    target = module_root / "src"
    target.mkdir()

    resolved = resolve_repo_root(str(target), [str(workspace_root)])

    assert resolved == str(workspace_root.resolve())


def test_repo_resolver_falls_back_to_workspace_prefix(tmp_path: Path) -> None:
    """git/빌드 마커가 없으면 워크스페이스 최장 prefix를 사용해야 한다."""
    workspace_root = tmp_path / "mono"
    workspace_root.mkdir()
    nested = workspace_root / "libs" / "common"
    nested.mkdir(parents=True)

    resolved = resolve_repo_root(str(nested), [str(workspace_root)])

    assert resolved == str(workspace_root.resolve())


def test_repo_resolver_no_build_tool_single_project_is_resolved(tmp_path: Path) -> None:
    """빌드 도구가 없는 단일 프로젝트도 등록 워크스페이스로 정상 해석해야 한다."""
    workspace_root = tmp_path / "single-project"
    workspace_root.mkdir()
    target = workspace_root / "src" / "domain"
    target.mkdir(parents=True)

    resolved = resolve_repo_root(str(target), [str(workspace_root)])

    assert resolved == str(workspace_root.resolve())


def test_repo_resolver_multimodule_prefers_registered_module_workspace(tmp_path: Path) -> None:
    """멀티모듈에서 하위 모듈이 등록되어 있으면 해당 모듈 경계를 우선 사용해야 한다."""
    mono_root = tmp_path / "mono"
    mono_root.mkdir()
    module_a = mono_root / "services" / "a"
    module_b = mono_root / "services" / "b"
    module_a.mkdir(parents=True)
    module_b.mkdir(parents=True)
    (module_a / "package.json").write_text("{\"name\":\"a\"}\n", encoding="utf-8")
    (module_b / "package.json").write_text("{\"name\":\"b\"}\n", encoding="utf-8")
    target = module_a / "src"
    target.mkdir()

    resolved = resolve_repo_root(str(target), [str(mono_root), str(module_a)])

    assert resolved == str(module_a.resolve())


def test_repo_resolver_prefers_exact_registered_workspace_before_build_scan(tmp_path: Path) -> None:
    """입력이 등록 워크스페이스와 정확히 일치하면 하위 빌드 마커 탐색보다 우선해야 한다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "module-a").mkdir()
    (workspace_root / "module-b").mkdir()
    (workspace_root / "module-a" / "package.json").write_text("{\"name\":\"a\"}\n", encoding="utf-8")
    (workspace_root / "module-b" / "package.json").write_text("{\"name\":\"b\"}\n", encoding="utf-8")

    resolved = resolve_repo_root(str(workspace_root), [str(workspace_root)])

    assert resolved == str(workspace_root.resolve())


def test_repo_resolver_raises_when_no_workspace_matches(tmp_path: Path) -> None:
    """어떤 워크스페이스에도 속하지 않으면 명시 오류를 반환해야 한다."""
    isolated = tmp_path / "isolated"
    isolated.mkdir()

    with pytest.raises(ValidationError) as captured:
        resolve_repo_root(str(isolated), [])

    assert captured.value.context.code == "ERR_REPO_NOT_FOUND"


def test_repo_resolver_raises_when_build_root_is_ambiguous(tmp_path: Path) -> None:
    """동일 깊이 빌드 루트가 다수면 모호성 오류를 반환해야 한다."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    left = workspace_root / "module-a"
    right = workspace_root / "module-b"
    left.mkdir()
    right.mkdir()
    (left / "pyproject.toml").write_text("[project]\nname='a'\n", encoding="utf-8")
    (right / "pyproject.toml").write_text("[project]\nname='b'\n", encoding="utf-8")

    with pytest.raises(ValidationError) as captured:
        resolve_repo_root(str(workspace_root), [str(tmp_path)])

    assert captured.value.context.code == "ERR_REPO_AMBIGUOUS"


def test_repo_resolver_accepts_file_input_path(tmp_path: Path) -> None:
    """파일 경로가 입력되어도 부모 디렉터리 기준으로 정상 해석해야 한다."""
    repo_root = tmp_path / "repo-file-input"
    source_dir = repo_root / "src"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "main.py"
    source_file.write_text("print('ok')\n", encoding="utf-8")

    resolved = resolve_repo_root(str(source_file), [str(repo_root)])

    assert resolved == str(repo_root.resolve())


def test_repo_resolver_prefers_longest_workspace_prefix(tmp_path: Path) -> None:
    """중첩 워크스페이스가 동시에 등록되면 더 긴 경계를 선택해야 한다."""
    mono_root = tmp_path / "mono-prefix"
    nested_repo = mono_root / "services" / "checkout"
    target = nested_repo / "src"
    target.mkdir(parents=True)

    resolved = resolve_repo_root(str(target), [str(mono_root), str(nested_repo)])

    assert resolved == str(nested_repo.resolve())


def test_repo_resolver_ignores_invalid_workspace_entries(tmp_path: Path) -> None:
    """빈 문자열/비존재 경로가 섞여도 유효 경로로 정상 판별해야 한다."""
    workspace_root = tmp_path / "workspace"
    target = workspace_root / "pkg" / "feature"
    target.mkdir(parents=True)

    resolved = resolve_repo_root(
        str(target),
        [
            "",
            "   ",
            str(tmp_path / "missing-workspace"),
            str(workspace_root),
        ],
    )

    assert resolved == str(workspace_root.resolve())


@pytest.mark.skipif(os.name == "nt", reason="심볼릭 링크 권한/동작 차이로 POSIX에서만 검증")
def test_repo_resolver_resolves_symlink_path(tmp_path: Path) -> None:
    """입력이 심볼릭 링크여도 실제 경계 기준으로 repo를 정확히 판별해야 한다."""
    workspace_root = tmp_path / "workspace-symlink"
    real_target = workspace_root / "apps" / "api"
    real_target.mkdir(parents=True)
    symlink_path = tmp_path / "api-link"
    symlink_path.symlink_to(real_target)

    resolved = resolve_repo_root(str(symlink_path), [str(workspace_root)])

    assert resolved == str(workspace_root.resolve())
