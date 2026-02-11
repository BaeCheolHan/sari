import pytest
import zlib
from sari.core.search_engine import SearchEngine
from sari.core.models import SearchOptions

pytestmark = pytest.mark.gate


def _build_engine():
    engine = SearchEngine.__new__(SearchEngine)
    engine.db = type("DummyDB", (), {"settings": None})()
    engine._snippet_cache = {}
    engine._snippet_lru = []
    return engine


def test_process_sqlite_rows_handles_legacy_shape():
    engine = _build_engine()
    rows = [("root-1/a.py", "root-1", "repo1", 100, 12, "hello world")]
    hits = SearchEngine._process_sqlite_rows(engine, rows, SearchOptions(query="hello"))
    assert len(hits) == 1
    assert hits[0].repo == "repo1"
    assert hits[0].mtime == 100
    assert hits[0].size == 12


def test_process_sqlite_rows_handles_fts_shape():
    engine = _build_engine()
    rows = [("root-1/a.py", "a.py", "root-1", "repo1", 100, 12, "hello world")]
    hits = SearchEngine._process_sqlite_rows(engine, rows, SearchOptions(query="hello"))
    assert len(hits) == 1
    assert hits[0].repo == "repo1"
    assert hits[0].mtime == 100
    assert hits[0].size == 12


def test_snippet_for_decompresses_zlib_bytes():
    engine = _build_engine()
    raw = "alpha line\nkeyword target\nomega line"
    compressed = zlib.compress(raw.encode("utf-8"))
    snippet = SearchEngine._snippet_for(engine, "root-1/a.txt", "keyword", compressed)
    assert "keyword" in snippet.lower()


def test_snippet_for_keeps_match_context_when_truncating():
    class _Settings:
        SNIPPET_MAX_BYTES = 128
        SNIPPET_CACHE_SIZE = 0

    engine = _build_engine()
    engine.db.settings = _Settings()
    content = ("A" * 400) + "\nneedle_here\n" + ("B" * 400)
    snippet = SearchEngine._snippet_for(engine, "root-1/log.txt", "needle_here", content)
    assert "needle_here" in snippet


def test_fts_query_preserves_hyphenated_term():
    engine = _build_engine()
    fts = SearchEngine._fts_query(engine, "my-special-func")
    assert fts == '"my-special-func"'


def test_fts_query_preserves_special_prefix_symbols():
    engine = _build_engine()
    fts = SearchEngine._fts_query(engine, "$variable @decorator #tag")
    assert '"$variable"' in fts
    assert '"@decorator"' in fts
    assert '"#tag"' in fts


def test_fts_query_drops_invalid_trailing_near_operator():
    engine = _build_engine()
    fts = SearchEngine._fts_query(engine, "foo NEAR")
    assert "NEAR" not in fts
    assert '"foo"' in fts
