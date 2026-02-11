from __future__ import annotations

from pathlib import Path

from sari.core.models import SearchOptions
from sari.core.ranking import snippet_around
from sari.core.search_engine import SearchEngine


def _insert_file(cur, row):
    cur.execute(
        """
        INSERT INTO files (
            path, rel_path, root_id, repo, mtime, size, content, hash, fts_content,
            last_seen_ts, deleted_ts, status, error, parse_status, parse_error,
            ast_status, ast_reason, is_binary, is_minified, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row,
    )


def test_read_file_supports_abs_path_and_db_path_lookup(db, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "src" / "m.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("print('ok')", encoding="utf-8")
    rid = "rid-lookup"
    db.upsert_root(rid, str(ws), str(ws))
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            str(f),
            f"{rid}/src/m.py",
            rid,
            "repo",
            1,
            10,
            "print('ok')",
            "h",
            "print ok",
            0,
            0,
            "ok",
            "",
            "ok",
            "",
            "none",
            "none",
            0,
            0,
            "{}",
        ),
    )
    db._write.commit()
    assert db.read_file(str(f)) == "print('ok')"
    assert db.read_file(f"{rid}/src/m.py") == "print('ok')"


def test_read_file_non_utf8_bytes_roundtrip_without_loss(db):
    rid = "rid-bin"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    raw = b"\xff\x00\x80A"
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            "rid-bin/blob.bin",
            "blob.bin",
            rid,
            "repo",
            1,
            len(raw),
            raw,
            "h",
            "",
            0,
            0,
            "ok",
            "",
            "ok",
            "",
            "none",
            "none",
            1,
            0,
            "{}",
        ),
    )
    db._write.commit()
    text = db.read_file("rid-bin/blob.bin")
    assert isinstance(text, str)
    assert text.encode("latin-1") == raw


def test_prune_stale_data_handles_large_active_paths_without_sql_var_overflow(db):
    rid = "rid-prune"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    active_paths = [f"{rid}/f{i}.py" for i in range(33000)]
    db.prune_stale_data(rid, active_paths)


def test_repository_path_pattern_matches_db_path_style_rel_path(db):
    rid = "rid-path"
    ws = Path("/tmp/ws-path")
    db.upsert_root(rid, str(ws), str(ws))
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            str(ws / "src" / "logic.py"),
            f"{rid}/src/logic.py",
            rid,
            "repo",
            1,
            10,
            "def logic(): pass",
            "h",
            "logic",
            0,
            0,
            "ok",
            "",
            "ok",
            "",
            "none",
            "none",
            0,
            0,
            "{}",
        ),
    )
    db._write.commit()
    hits, _ = db.search_repo.search(SearchOptions(query="logic", path_pattern="src/**", root_ids=[rid], limit=10))
    assert any("logic.py" in h.path for h in hits)


def test_repository_snippet_falls_back_to_content_when_fts_content_empty(db):
    snippet, count = db.search_repo._extract_snippet("", "token_here", "def token_here():\n    return 1\n")
    assert count >= 1
    assert "token_here" in snippet


def test_repository_search_uses_joined_importance_instead_of_correlated_subquery(db, monkeypatch):
    repo = db.search_repo
    captured: list[str] = []

    def fake_execute(sql, _params=None):
        captured.append(sql)
        class _Rows:
            def fetchall(self):
                return []
            def fetchone(self):
                return (0,)
        return _Rows()

    monkeypatch.setattr(repo, "execute", fake_execute)
    repo.search(SearchOptions(query="x", limit=5, offset=0))
    select_sql = next((s.lower() for s in captured if " from files f" in s.lower() and "select" in s.lower()), "")
    assert "left join" in select_sql
    assert "select max(importance_score)" not in select_sql


def test_snippet_around_respects_case_sensitive_mode():
    snippet = snippet_around("Alpha\nalpha", ["Alpha"], 2, highlight=True, case_sensitive=True)
    assert "L1: >>>Alpha<<<" in snippet
    assert "L2: >>>alpha<<<" not in snippet


def test_snippet_cache_does_not_duplicate_lru_entries():
    engine = SearchEngine.__new__(SearchEngine)
    engine.db = type("DummyDB", (), {"settings": type("S", (), {"SNIPPET_CACHE_SIZE": 8, "SNIPPET_MAX_BYTES": 5000})()})()
    engine._snippet_cache = {}
    engine._snippet_lru = []
    SearchEngine._snippet_for(engine, "p.py", "k", "k one")
    SearchEngine._snippet_for(engine, "p.py", "k", "k one")
    assert len(engine._snippet_lru) == 1


def test_tantivy_escape_query_does_not_destroy_advanced_syntax():
    from sari.core.engine.tantivy_engine import TantivyEngine
    engine = TantivyEngine.__new__(TantivyEngine)
    q = engine._escape_query("body:foo OR (bar baz)")
    assert ":" in q
    assert "(" in q
    assert ")" in q
    assert "\\:" not in q


def test_repo_candidates_query_does_not_scan_fts_content_column(db, monkeypatch):
    repo = db.search_repo
    captured = {"sql": ""}

    def fake_execute(sql, _params=None):
        captured["sql"] = sql
        class _Rows:
            def fetchall(self):
                return []
        return _Rows()

    monkeypatch.setattr(repo, "execute", fake_execute)
    repo.repo_candidates("abc", limit=3, root_ids=["rid"])
    assert "fts_content like" not in captured["sql"].lower()


def test_repo_candidates_escapes_wildcard_query_literals(db, monkeypatch):
    repo = db.search_repo
    captured = {"sql": "", "params": []}

    def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = list(params or [])
        class _Rows:
            def fetchall(self):
                return []
        return _Rows()

    monkeypatch.setattr(repo, "execute", fake_execute)
    repo.repo_candidates("%_", limit=3, root_ids=["rid"])
    assert " escape " in captured["sql"].lower()
    assert "%\\%\\_%" in captured["params"][0]


def test_snippet_cache_key_changes_when_content_changes():
    engine = SearchEngine.__new__(SearchEngine)
    engine.db = type("DummyDB", (), {"settings": type("S", (), {"SNIPPET_CACHE_SIZE": 8, "SNIPPET_MAX_BYTES": 5000})()})()
    engine._snippet_cache = {}
    engine._snippet_lru = []
    s1 = SearchEngine._snippet_for(engine, "p.py", "needle", "aaa\nneedle\nbbb")
    s2 = SearchEngine._snippet_for(engine, "p.py", "needle", "xxx\nneedle\nyyy")
    assert s1 != s2


def test_read_file_returns_none_when_content_is_null(db):
    rid = "rid-null"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            "rid-null/a.py",
            "a.py",
            rid,
            "repo",
            1,
            10,
            None,
            "h",
            "",
            0,
            0,
            "ok",
            "",
            "ok",
            "",
            "none",
            "none",
            0,
            0,
            "{}",
        ),
    )
    db._write.commit()
    assert db.read_file("rid-null/a.py") is None
