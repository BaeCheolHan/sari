"""repo 판별 정책의 정확도를 수치화하고 98% 게이트를 강제한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sari.core.exceptions import ValidationError
from sari.core.repo.resolver import resolve_repo_root


@dataclass(frozen=True)
class RepoResolverCase:
    """repo 판별 단일 시나리오를 표현한다."""

    name: str
    input_path: str
    workspace_paths: list[str]
    expected_repo: str | None
    expected_error_code: str | None


def _build_accuracy_cases(tmp_path: Path) -> list[RepoResolverCase]:
    """정확도 측정을 위한 라벨링 케이스 집합을 생성한다."""
    cases: list[RepoResolverCase] = []

    for index in range(25):
        repo_root = tmp_path / "git" / f"repo_{index}"
        target = repo_root / "src" / "pkg"
        target.mkdir(parents=True, exist_ok=True)
        (repo_root / ".git").mkdir(exist_ok=True)
        cases.append(
            RepoResolverCase(
                name=f"git_{index}",
                input_path=str(target),
                workspace_paths=[str(repo_root)],
                expected_repo=str(repo_root.resolve()),
                expected_error_code=None,
            )
        )

    for index in range(25):
        workspace_root = tmp_path / "build_only" / f"workspace_{index}"
        module_root = workspace_root / "services" / f"module_{index}"
        target = module_root / "src"
        target.mkdir(parents=True, exist_ok=True)
        (module_root / "package.json").write_text("{\"name\":\"module\"}\n", encoding="utf-8")
        cases.append(
            RepoResolverCase(
                name=f"build_marker_{index}",
                input_path=str(target),
                workspace_paths=[str(workspace_root)],
                expected_repo=str(workspace_root.resolve()),
                expected_error_code=None,
            )
        )

    for index in range(20):
        workspace_root = tmp_path / "fallback" / f"workspace_{index}"
        target = workspace_root / "libs" / "common" / f"part_{index}"
        target.mkdir(parents=True, exist_ok=True)
        cases.append(
            RepoResolverCase(
                name=f"workspace_prefix_{index}",
                input_path=str(target),
                workspace_paths=[str(workspace_root)],
                expected_repo=str(workspace_root.resolve()),
                expected_error_code=None,
            )
        )

    for index in range(20):
        mono_root = tmp_path / "mono" / f"mono_{index}"
        module_a = mono_root / "services" / "a"
        module_b = mono_root / "services" / "b"
        target = module_a / "src" / "feature"
        target.mkdir(parents=True, exist_ok=True)
        module_b.mkdir(parents=True, exist_ok=True)
        (module_a / "package.json").write_text("{\"name\":\"a\"}\n", encoding="utf-8")
        (module_b / "package.json").write_text("{\"name\":\"b\"}\n", encoding="utf-8")
        cases.append(
            RepoResolverCase(
                name=f"multimodule_registered_{index}",
                input_path=str(target),
                workspace_paths=[str(mono_root), str(module_a)],
                expected_repo=str(module_a.resolve()),
                expected_error_code=None,
            )
        )

    for index in range(5):
        unresolved = tmp_path / "unresolved" / f"path_{index}"
        unresolved.mkdir(parents=True, exist_ok=True)
        cases.append(
            RepoResolverCase(
                name=f"not_found_{index}",
                input_path=str(unresolved),
                workspace_paths=[],
                expected_repo=None,
                expected_error_code="ERR_REPO_NOT_FOUND",
            )
        )

    for index in range(5):
        ambiguous_root = tmp_path / "ambiguous" / f"root_{index}"
        left = ambiguous_root / "module-a"
        right = ambiguous_root / "module-b"
        left.mkdir(parents=True, exist_ok=True)
        right.mkdir(parents=True, exist_ok=True)
        (left / "pyproject.toml").write_text("[project]\nname='a'\n", encoding="utf-8")
        (right / "pyproject.toml").write_text("[project]\nname='b'\n", encoding="utf-8")
        cases.append(
            RepoResolverCase(
                name=f"ambiguous_{index}",
                input_path=str(ambiguous_root),
                workspace_paths=[str(tmp_path)],
                expected_repo=None,
                expected_error_code="ERR_REPO_AMBIGUOUS",
            )
        )

    return cases


def test_repo_resolver_accuracy_gate(tmp_path: Path) -> None:
    """라벨링 데이터셋 기준 repo 판별 정확도는 98% 이상이어야 한다."""
    cases = _build_accuracy_cases(tmp_path)
    correct_count = 0

    for case in cases:
        try:
            resolved = resolve_repo_root(case.input_path, case.workspace_paths)
            if case.expected_repo is not None and resolved == case.expected_repo:
                correct_count += 1
        except ValidationError as error:
            if case.expected_error_code is not None and error.context.code == case.expected_error_code:
                correct_count += 1

    accuracy = correct_count / len(cases)
    assert accuracy >= 0.98, f"repo resolver accuracy is too low: {accuracy:.4f} ({correct_count}/{len(cases)})"
