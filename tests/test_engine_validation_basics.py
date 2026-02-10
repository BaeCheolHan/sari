from unittest.mock import MagicMock

from sari.core.parsers.factory import ParserFactory
from sari.core.utils import _normalize_engine_text


def test_python_parser_extracts_expected_symbols():
    parser = ParserFactory.get_parser(".py")
    assert parser is not None

    content = """
class Session:
    def request(self, method, url):
        return None
"""
    symbols, relations = parser.extract("sessions.py", content)
    names = {s.name for s in symbols}
    assert "Session" in names
    assert "request" in names
    assert isinstance(relations, list)


def test_scanner_excludes_nested_sari_workspace(tmp_path):
    from sari.core.indexer.scanner import Scanner
    from sari.core.settings import settings as global_settings

    (tmp_path / "file1.txt").write_text("parent", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    (child / ".sari").mkdir()
    (child / "file2.txt").write_text("child", encoding="utf-8")

    cfg = MagicMock()
    cfg.exclude_dirs = []
    cfg.settings = global_settings
    scanner = Scanner(cfg)

    entries = list(scanner.iter_file_entries(tmp_path))
    assert entries
    assert all(len(entry) == 3 for entry in entries)
    paths = [str(path.relative_to(tmp_path)) for path, _st, excluded in entries if not excluded]
    assert "file1.txt" in paths


def test_cjk_normalization_retains_korean_tokens():
    sample = """
# 네이버 egjs-grid
이 라이브러리는 레이아웃을 효율적으로 배치합니다.
"""
    normalized = _normalize_engine_text(sample)
    assert "네이버" in normalized
    assert "레이아웃" in normalized
