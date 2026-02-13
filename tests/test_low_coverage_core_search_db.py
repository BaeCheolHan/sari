from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from sari.core.db.main import LocalSearchDB
from sari.core.models import SearchOptions
from sari.core.db.models import db_proxy


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
    hits, meta = repo.search(opts)
    assert len(hits) == 1
    assert meta["total"] == 1

    # Count fallback path in _calculate_total_count
    original_execute = repo.execute

    def flaky_count(sql, params=None):
        if "SELECT COUNT(1) FROM files f WHERE" in sql:
            raise RuntimeError("count fail")
        return original_execute(sql, params)

    monkeypatch.setattr(repo, "execute", flaky_count)
    hits2, meta2 = repo.search(opts)
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
    hits, meta = repo.search(SimpleNamespace(query="", total_mode="approx"))
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
        repo.search(opts)
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
    sql4, p4 = db.apply_root_filter("SELECT repo, COUNT(*) FROM files GROUP BY repo ORDER BY repo", "rid")
    assert "WHERE root_id = ? GROUP BY repo ORDER BY repo" in sql4
    assert p4 == ["rid"]

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


def test_local_db_swap_db_file_does_not_detach_when_attach_fails(db, tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"not-a-db")
    calls: list[str] = []

    class _FakeConn:
        in_transaction = False

        def execute(self, sql, _params=None):
            calls.append(str(sql))
            if str(sql).startswith("ATTACH DATABASE"):
                raise RuntimeError("attach failed")
            return self

        def fetchone(self):
            return (0,)

    monkeypatch.setattr(db.db, "connection", lambda: _FakeConn())
    with pytest.raises(RuntimeError):
        db.swap_db_file(str(snapshot))
    assert not any(str(sql).startswith("DETACH DATABASE snapshot") for sql in calls)


def test_local_db_swap_db_file_rolls_back_on_table_copy_failure(db, tmp_path):
    rid = "root-main"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    assert db._write.execute("SELECT COUNT(1) FROM files").fetchone()[0] == 0

    snapshot_path = tmp_path / "snapshot.db"
    snap = LocalSearchDB(str(snapshot_path))
    try:
        snap.upsert_root(rid, "/tmp/ws", "/tmp/ws")
        cur = snap._write.cursor()
        _insert_file(
            cur,
            (
                f"{rid}/x.py",
                "x.py",
                rid,
                "repo1",
                11,
                20,
                "print('x')",
                "h1",
                "print x",
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
        snap._write.commit()
        snap._write.execute("DROP TABLE symbols")
        snap._write.commit()
    finally:
        snap.close_all()

    with pytest.raises(sqlite3.DatabaseError):
        db.swap_db_file(str(snapshot_path))

    # files copy must be rolled back as a unit
    assert db._write.execute("SELECT COUNT(1) FROM files").fetchone()[0] == 0


def test_search_semantic_skips_corrupted_vector_blob(db):
    rid = "root-sem"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "file", f"{rid}/ok.py", "h1", "m", b"\x00\x00\x80?\x00\x00\x00@", 0, 0),
    )
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "file", f"{rid}/bad.py", "h2", "m", b"\x00\x01", 0, 0),
    )
    db._write.commit()

    hits = db.search_repo.search_semantic([1.0, 0.0], limit=5, root_ids=[rid])
    assert any(h.path == f"{rid}/ok.py" for h in hits)


def test_local_db_sql_paths_for_repo_stats_and_file_queries(db):
    rid = "root-sql"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    _insert_file(
        cur,
        (
            f"{rid}/src/a.py",
            "src/a.py",
            rid,
            "repo-a",
            11,
            20,
            "print('a')",
            "h1",
            "print a",
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
    _insert_file(
        cur,
        (
            f"{rid}/src/b.py",
            "src/b.py",
            rid,
            "repo-a",
            12,
            21,
            "print('b')",
            "h2",
            "print b",
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
    db.update_stats()

    stats = db.get_repo_stats(root_ids=[rid])
    assert stats
    assert list(stats.values())[0] == 2

    assert db.read_file(f"{rid}/src/a.py") == "print('a')"
    hits = db.search_files("src/", limit=5)
    assert len(hits) >= 2

    db.prune_stale_data(rid, active_paths=[f"{rid}/src/a.py"])
    rows_after_prune = db._write.execute("SELECT path FROM files WHERE root_id = ?", (rid,)).fetchall()
    assert len(rows_after_prune) == 1

    db.prune_stale_data(rid, active_paths=[])
    rows_after_full = db._write.execute("SELECT COUNT(1) FROM files WHERE root_id = ?", (rid,)).fetchone()[0]
    assert rows_after_full == 0


def test_search_semantic_zero_query_vector_returns_empty_without_division_error(db, monkeypatch):
    rid = "root-sem-zero"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "file", f"{rid}/ok.py", "h1", "m", b"\x00\x00\x80?\x00\x00\x00@", 0, 0),
    )
    db._write.commit()
    import builtins
    real_import = builtins.__import__

    def no_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_numpy)
    assert db.search_repo.search_semantic([0.0, 0.0], limit=5, root_ids=[rid]) == []


def test_search_semantic_uses_repo_label_instead_of_root_id(db):
    rid = "root-sem-label"
    db.upsert_root(rid, "/tmp/workspace-a", "/tmp/workspace-a", label="workspace-a")
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "file", f"{rid}/ok.py", "h1", "m", b"\x00\x00\x80?\x00\x00\x00@", 0, 0),
    )
    db._write.commit()
    hits = db.search_repo.search_semantic([1.0, 0.0], limit=5, root_ids=[rid])
    assert hits
    assert hits[0].repo == "workspace-a"


def test_search_semantic_hit_reason_uses_per_row_entity_type(db):
    rid = "root-sem-type"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "file", f"{rid}/a.py", "h1", "m", b"\x00\x00\x80?\x00\x00\x00@", 0, 0),
    )
    db._write.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid, "symbol", f"{rid}/Sym", "h2", "m", b"\x00\x00\x80?\x00\x00\x00@", 0, 0),
    )
    db._write.commit()
    hits = db.search_repo.search_semantic([1.0, 0.0], limit=10, root_ids=[rid])
    reasons = {h.path: h.hit_reason for h in hits}
    assert reasons[f"{rid}/a.py"] == "Semantic (file)"
    assert reasons[f"{rid}/Sym"] == "Semantic (symbol)"


def test_update_stats_does_not_commit_when_outer_transaction_open(db):
    rid = "root-tx"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    conn = db._write
    conn.execute("BEGIN")
    cur = conn.cursor()
    _insert_file(
        cur,
        (
            f"{rid}/tx.py",
            "tx.py",
            rid,
            "repo",
            1,
            1,
            "x",
            "h",
            "x",
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
    db.update_stats()
    conn.execute("ROLLBACK")
    count = db._write.execute("SELECT COUNT(1) FROM files WHERE path = ?", (f"{rid}/tx.py",)).fetchone()[0]
    assert count == 0


def test_local_db_bind_proxy_false_does_not_rebind_global_proxy(tmp_path):
    main = LocalSearchDB(str(tmp_path / "main.db"), bind_proxy=True)
    original = db_proxy.obj
    snap = LocalSearchDB(str(tmp_path / "snap.db"), bind_proxy=False)
    try:
        assert db_proxy.obj is original
    finally:
        snap.close_all()
        main.close_all()


def test_upsert_root_works_with_bind_proxy_false(tmp_path):
    main = LocalSearchDB(str(tmp_path / "main.db"), bind_proxy=True)
    snap = LocalSearchDB(str(tmp_path / "snap.db"), bind_proxy=False)
    try:
        snap.upsert_root("rid-snap", "/tmp/snap", "/tmp/snap", label="snap")
        row = snap._write.execute(
            "SELECT root_id, label FROM roots WHERE root_id = ?",
            ("rid-snap",),
        ).fetchone()
        assert row is not None
        assert row[0] == "rid-snap"
        assert row[1] == "snap"
    finally:
        snap.close_all()
        main.close_all()


def test_upsert_root_preserves_created_timestamp(db):
    db.upsert_root("rid-created", "/tmp/a", "/tmp/a", label="a")
    created_1 = db._write.execute("SELECT created_ts FROM roots WHERE root_id = ?", ("rid-created",)).fetchone()[0]
    db.upsert_root("rid-created", "/tmp/a", "/tmp/a", label="b")
    row = db._write.execute("SELECT created_ts, label FROM roots WHERE root_id = ?", ("rid-created",)).fetchone()
    assert row[0] == created_1
    assert row[1] == "b"


def test_upsert_files_turbo_rel_path_handles_windows_separator(db):
    rid = "root-win"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    row = (r"C:\repo\src\main.py", "", rid, "repo", 1, 10, "x", "h", "x", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}")
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()
    rel = db._write.execute("SELECT rel_path FROM files WHERE path = ?", (r"c:/repo/src/main.py",)).fetchone()[0]
    assert rel == "main.py"
