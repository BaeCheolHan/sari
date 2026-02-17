"""repo 경계 판별 유틸을 제공한다."""

from __future__ import annotations

import os
from pathlib import Path

from sari.core.exceptions import ErrorContext, ValidationError


_BUILD_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "WORKSPACE",
    "MODULE.bazel",
    "nx.json",
    "pnpm-workspace.yaml",
    "turbo.json",
    "composer.json",
)
_SKIP_DIR_NAMES: set[str] = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
}


def resolve_repo_root(repo_or_path: str, workspace_paths: list[str]) -> str:
    """입력 경로에서 repo 루트를 결정론적으로 판별한다."""
    normalized_input = _normalize_input_path(repo_or_path)
    normalized_workspaces = _normalize_workspace_paths(workspace_paths)
    exact_workspace_match = _find_exact_workspace_match(normalized_input, normalized_workspaces)
    if exact_workspace_match is not None:
        return str(exact_workspace_match)

    git_candidate = _find_git_root(normalized_input)
    if git_candidate is not None:
        return _finalize_repo_candidate(git_candidate, normalized_input, normalized_workspaces)

    build_candidate = _find_deepest_build_root(normalized_input)
    if build_candidate is not None:
        return _finalize_repo_candidate(build_candidate, normalized_input, normalized_workspaces)

    workspace_candidate = _find_longest_workspace_prefix(normalized_input, normalized_workspaces)
    if workspace_candidate is not None:
        return str(workspace_candidate)

    raise ValidationError(
        ErrorContext(
            code="ERR_REPO_NOT_FOUND",
            message="repo is not registered workspace",
        )
    )


def is_path_within_repo_boundary(repo_root: str, path_value: str | Path) -> bool:
    """주어진 경로가 repo 경계 내부인지 여부를 반환한다."""
    try:
        root = Path(repo_root).resolve()
        target = Path(path_value).resolve()
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_input_path(repo_or_path: str) -> Path:
    """입력 경로를 절대 디렉터리 경로로 정규화한다."""
    normalized = Path(repo_or_path).expanduser().resolve()
    if not normalized.exists():
        raise ValidationError(
            ErrorContext(
                code="ERR_REPO_NOT_FOUND",
                message="repo path does not exist",
            )
        )
    if normalized.is_file():
        return normalized.parent
    if not normalized.is_dir():
        raise ValidationError(
            ErrorContext(
                code="ERR_REPO_NOT_FOUND",
                message="repo path is not a directory",
            )
        )
    return normalized


def _normalize_workspace_paths(workspace_paths: list[str]) -> list[Path]:
    """워크스페이스 경로 목록을 정규화한다."""
    normalized: list[Path] = []
    for raw_path in workspace_paths:
        stripped = str(raw_path).strip()
        if stripped == "":
            continue
        candidate = Path(stripped).expanduser().resolve()
        if not candidate.exists() or not candidate.is_dir():
            continue
        normalized.append(candidate)
    # 가장 긴 경계를 먼저 평가하기 위해 경로 길이 기준 정렬한다.
    normalized.sort(key=lambda item: len(item.parts), reverse=True)
    return normalized


def _find_git_root(start: Path) -> Path | None:
    """현재 경로에서 상위로 올라가며 git 루트를 찾는다."""
    current = start
    while True:
        marker = current / ".git"
        if marker.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _find_deepest_build_root(start: Path) -> Path | None:
    """빌드 마커를 재귀 탐색해 가장 깊은 루트를 반환한다."""
    candidates: set[Path] = set()
    current = start
    while True:
        if _contains_build_marker(current):
            candidates.add(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

    for current_root, dir_names, file_names in os.walk(str(start)):
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIR_NAMES]
        if _has_any_marker(file_names):
            candidates.add(Path(current_root).resolve())

    if len(candidates) == 0:
        return None
    sorted_candidates = sorted(candidates, key=lambda item: (len(item.parts), str(item)), reverse=True)
    deepest = sorted_candidates[0]
    if len(sorted_candidates) == 1:
        return deepest
    same_depth = [item for item in sorted_candidates if len(item.parts) == len(deepest.parts)]
    if len(same_depth) > 1:
        raise ValidationError(
            ErrorContext(
                code="ERR_REPO_AMBIGUOUS",
                message="multiple candidate repo roots detected",
            )
        )
    return deepest


def _contains_build_marker(directory: Path) -> bool:
    """디렉터리에 빌드 마커가 존재하는지 반환한다."""
    for marker in _BUILD_MARKERS:
        if (directory / marker).exists():
            return True
    return False


def _has_any_marker(file_names: list[str]) -> bool:
    """디렉터리 파일 목록이 빌드 마커를 포함하는지 반환한다."""
    names = set(file_names)
    for marker in _BUILD_MARKERS:
        if marker in names:
            return True
    return False


def _find_longest_workspace_prefix(path_value: Path, workspace_paths: list[Path]) -> Path | None:
    """입력 경로를 포함하는 가장 긴 워크스페이스 경계를 반환한다."""
    for workspace_path in workspace_paths:
        if _is_path_descendant(path_value, workspace_path):
            return workspace_path
    return None


def _find_exact_workspace_match(path_value: Path, workspace_paths: list[Path]) -> Path | None:
    """입력 경로와 정확히 일치하는 워크스페이스를 반환한다."""
    for workspace_path in workspace_paths:
        if workspace_path == path_value:
            return workspace_path
    return None


def _is_path_descendant(target: Path, root: Path) -> bool:
    """target이 root 내부 경로인지 판단한다."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _finalize_repo_candidate(candidate: Path, original_input: Path, workspace_paths: list[Path]) -> str:
    """git/build 후보를 워크스페이스 경계에 맞춰 최종 repo로 확정한다."""
    for workspace_path in workspace_paths:
        if workspace_path == candidate:
            return str(workspace_path)

    candidate_prefix = _find_longest_workspace_prefix(candidate, workspace_paths)
    if candidate_prefix is not None:
        return str(candidate_prefix)

    input_prefix = _find_longest_workspace_prefix(original_input, workspace_paths)
    if input_prefix is not None:
        return str(input_prefix)

    raise ValidationError(
        ErrorContext(
            code="ERR_REPO_NOT_FOUND",
            message="repo is not registered workspace",
        )
    )
