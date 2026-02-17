"""LSP 경로 정규화 유틸리티를 검증한다."""

from __future__ import annotations

from sari.lsp.path_normalizer import normalize_repo_relative_path


def test_normalize_repo_relative_path_unifies_separators_and_dot_prefix() -> None:
    """경로 구분자와 선행 ./ 는 정규화되어야 한다."""
    assert normalize_repo_relative_path("./src\\app//main.py") == "src/app/main.py"


def test_normalize_repo_relative_path_keeps_clean_relative_path() -> None:
    """이미 정규화된 상대 경로는 그대로 유지되어야 한다."""
    assert normalize_repo_relative_path("pkg/service/core.go") == "pkg/service/core.go"
