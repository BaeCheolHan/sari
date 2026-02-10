from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from sari.core.models import SearchOptions


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


def test_search_repository_fallback_count_and_snippet_branches(db, monkeypatch):
    rid = "root-sr"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            f"{rid}/src/logic.py",
            "src/logic.py",
            rid,
            "repo1",
            11,
            20,
            "def logic(): pass",
            "h1",
            "logic code",
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

    # Drop symbols table so importance subquery fails -> fallback SQL path in _execute_search_query
    db._write.execute("DROP TABLE IF EXISTS symbols")
    db._write.commit()

    repo = db.search_repo
    opts = SearchOptions(query="logic", repo="repo1", limit=10, offset=0, total_mode="exact", root_ids=[rid])
    hits, meta = repo.search_v2(opts)
    assert len(hits) == 1
    assert meta["total"] == 1

    # Count fallback path in _calculate_total_count
    original_execute = repo.execute

    def flaky_count(sql, params=None):
        if "SELECT COUNT(1) FROM files f WHERE" in sql:
            raise RuntimeError("count fail")
        return original_execute(sql, params)

    monkeypatch.setattr(repo, "execute", flaky_count)
    hits2, meta2 = repo.search_v2(opts)
    assert len(hits2) == 1
    assert meta2["total"] == len(hits2)

    # _extract_snippet branch coverage: no match and exception path
    snippet, count = repo._extract_snippet("hello world", "missing")
    assert snippet == ""
    assert count == 0

    class _BadStr:
        def __str__(self):
            raise ValueError("boom")

    snippet2, count2 = repo._extract_snippet(_BadStr(), "q")
    assert snippet2 == ""
    assert count2 == 0


def test_search_repository_empty_query_and_semantic_empty_rows(db, monkeypatch):
    repo = db.search_repo

    # search_v2 empty query short-circuit
    hits, meta = repo.search_v2(SimpleNamespace(query="", total_mode="approx"))
    assert hits == []
    assert meta["total"] == 0
    assert meta["total_mode"] == "approx"

    # search_semantic: empty result path
    class _C:
        def fetchall(self):
            return []

    monkeypatch.setattr(repo, "execute", lambda _sql, _params=None: _C())
    assert repo.search_semantic([1.0, 0.0], limit=5) == []


def test_search_repository_does_not_swallow_unrelated_operational_errors(db, monkeypatch):
    repo = db.search_repo
    opts = SearchOptions(query="x", limit=5, offset=0)
    calls = {"n": 0}

    def boom(_sql, _params=None):
        calls["n"] += 1
        raise sqlite3.OperationalError("syntax error")

    monkeypatch.setattr(repo, "execute", boom)
    with pytest.raises(sqlite3.OperationalError):
        repo.search_v2(opts)
    assert calls["n"] == 1


def test_local_db_apply_root_filter_and_path_resolution(db, monkeypatch, tmp_path):
    # apply_root_filter branches
    assert db.apply_root_filter("", None) == ("", [])

    sql1, p1 = db.apply_root_filter("SELECT * FROM files", None)
    assert "WHERE 1=1" in sql1
    assert p1 == []

    sql2, p2 = db.apply_root_filter("SELECT * FROM files", "rid")
    assert "WHERE root_id = ?" in sql2
    assert p2 == ["rid"]

    sql3, p3 = db.apply_root_filter("SELECT * FROM files WHERE deleted_ts = 0", "rid")
    assert "AND root_id = ?" in sql3
    assert p3 == ["rid"]

    # _resolve_db_path absolute path branch
    root = tmp_path / "ws"
    root.mkdir()
    p = root / "a.py"
    p.write_text("print('x')", encoding="utf-8")

    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.find_root_for_path", lambda _p: str(root))
    monkeypatch.setattr("sari.core.workspace.WorkspaceManager.root_id", lambda _r: "rid")
    assert db._resolve_db_path(str(p)) == "rid/a.py"

    # _get_real_conn branches for cursor-like objects
    class _WithConn:
        connection = "conn-sentinel"

    class _WithExec:
        def execute(self, *_a, **_k):
            return None

    assert db._get_real_conn(_WithConn()) == "conn-sentinel"
    ex = _WithExec()
    assert db._get_real_conn(ex) is ex


def test_local_db_swap_db_file_missing_is_noop(db, tmp_path):
    missing = tmp_path / "missing_snapshot.db"
    # should return without raising
    db.swap_db_file(str(missing))
