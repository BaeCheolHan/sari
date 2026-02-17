"""CI LSP 하드게이트 언어 목록 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.language_registry import get_enabled_language_names


def _read_required_languages(file_path: Path) -> tuple[str, ...]:
    """언어 목록 파일을 파싱해 정규화된 튜플로 반환한다."""
    values: list[str] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().lower()
        if line == "" or line.startswith("#"):
            continue
        values.append(line)
    return tuple(values)


def test_ci_required_languages_file_matches_registry() -> None:
    """CI 언어 SSOT 파일은 레지스트리와 정확히 일치해야 한다."""
    root = Path(__file__).resolve().parents[2]
    lang_file = root / "tools" / "ci" / "lsp_required_languages.txt"
    assert lang_file.exists()

    listed = _read_required_languages(lang_file)
    enabled = get_enabled_language_names()

    assert len(listed) >= 35
    assert len(set(listed)) == len(listed)
    assert tuple(sorted(listed)) == tuple(sorted(enabled))
