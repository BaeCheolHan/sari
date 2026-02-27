"""경고 발생을 줄이기 위한 설정/로더 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path
import warnings

from sari.services.collection.l3.l3_tree_sitter_outline import TreeSitterOutlineExtractor


def test_alembic_ini_declares_path_separator_os() -> None:
    """Alembic deprecation 경고 방지를 위해 path_separator를 고정해야 한다."""
    project_root = Path(__file__).resolve().parents[3]
    alembic_ini = project_root / "alembic.ini"
    content = alembic_ini.read_text(encoding="utf-8")

    assert "path_separator = os" in content


def test_tree_sitter_loader_suppresses_deprecated_get_language_warning() -> None:
    """get_language 내부 deprecation 경고는 로더 내부에서 국소 억제되어야 한다."""
    extractor = TreeSitterOutlineExtractor()
    extractor._get_language = lambda normalized_lang: (  # type: ignore[assignment]
        warnings.warn("Language(path, name) is deprecated. Use Language(ptr, name) instead.", FutureWarning),
        f"lang:{normalized_lang}",
    )[1]

    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        loaded = extractor._load_language("python")

    assert loaded == "lang:python"
    future_warnings = [entry for entry in records if issubclass(entry.category, FutureWarning)]
    assert future_warnings == []
