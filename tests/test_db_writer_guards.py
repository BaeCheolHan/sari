import pytest

from sari.core.db.main import LocalSearchDB
from sari.core.indexer.db_writer import DBWriter, DbTask


def _files_row(path: str, root_id: str) -> tuple:
    return (
        path,
        "a.py",
        root_id,
        "repo",
        100,
        10,
        "print('x')",
        "hash",
        "print x",
        100,
        0,
        "ok",
        "",
        "none",
        "",
        0,
        0,
        0,
        10,
        "{}",
    )


def test_db_writer_engine_failure_raises_and_rolls_back(tmp_path):
    db = LocalSearchDB(str(tmp_path / "w.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")

    class BadEngine:
        def upsert_documents(self, _docs):
            raise RuntimeError("engine down")

    db.set_engine(BadEngine())
    writer = DBWriter(db)
    conn = db._write
    cur = conn.cursor()
    cur.execute("BEGIN")
    with pytest.raises(RuntimeError):
        writer._process_batch(
            cur,
            [
                DbTask(
                    kind="upsert_files",
                    rows=[_files_row("root1/a.py", "root1")],
                    engine_docs=[{"id": "root1/a.py", "root_id": "root1"}],
                )
            ],
        )
    conn.rollback()

    row = db._read.execute("SELECT path FROM files WHERE path = ?", ("root1/a.py",)).fetchone()
    assert row is None
    db.close_all()


def test_write_gate_windows_lock_path(monkeypatch, tmp_path):
    import sari.core.indexer.db_writer as dbw

    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, nbytes):
            calls.append((fd, mode, nbytes))

    monkeypatch.setattr(dbw, "fcntl", None, raising=False)
    monkeypatch.setattr(dbw, "msvcrt", FakeMsvcrt)

    gate = dbw._WriteGate(str(tmp_path / "gate.db"))
    with gate:
        pass

    assert len(calls) == 2
    assert calls[0][1] == FakeMsvcrt.LK_LOCK
    assert calls[1][1] == FakeMsvcrt.LK_UNLCK


def test_db_writer_applies_update_last_seen_task(tmp_path):
    db = LocalSearchDB(str(tmp_path / "u.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")
    row = _files_row("root1/a.py", "root1")
    with db._lock:
        cur = db._write.cursor()
        db.upsert_files_tx(cur, [row])
        db._write.commit()

    writer = DBWriter(db)
    conn = db._write
    cur = conn.cursor()
    before = db._read.execute("SELECT last_seen_ts FROM files WHERE path = ?", ("root1/a.py",)).fetchone()[0]
    cur.execute("BEGIN")
    writer._process_batch(cur, [DbTask(kind="update_last_seen", paths=["root1/a.py"])])
    conn.commit()
    after = db._read.execute("SELECT last_seen_ts FROM files WHERE path = ?", ("root1/a.py",)).fetchone()[0]
    assert int(after) >= int(before)
    db.close_all()


def test_db_writer_flush_waits_for_processing(tmp_path):
    class DummyDB:
        def __init__(self):
            self.rows = []
            self.engine = None

        def upsert_files_tx(self, _cur, rows):
            time.sleep(0.05)
            self.rows.extend(rows)

    import time
    db = DummyDB()
    writer = DBWriter(db, max_batch=2, max_wait=0.01)
    writer.start()

    rows = [
        _files_row("root1/a1.py", "root1"),
        _files_row("root1/a2.py", "root1"),
        _files_row("root1/a3.py", "root1"),
    ]
    for row in rows:
        writer.enqueue(DbTask(kind="upsert_files", rows=[row]))

    assert writer.flush(timeout=2.0) is True
    assert len(db.rows) == 3

    writer.stop()


def test_db_writer_batches_engine_commit_per_batch(tmp_path):
    class FakeEngine:
        def __init__(self):
            self.upserts = []
            self.commits = 0

        def upsert_documents(self, docs, commit=True):
            self.upserts.append((list(docs), commit))

        def commit(self):
            self.commits += 1

    db = LocalSearchDB(str(tmp_path / "b.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")
    db.set_engine(FakeEngine())

    writer = DBWriter(db)
    conn = db._write
    cur = conn.cursor()
    cur.execute("BEGIN")
    writer._process_batch(
        cur,
        [
            DbTask(
                kind="upsert_files",
                rows=[_files_row("root1/a.py", "root1")],
                engine_docs=[{"id": "root1/a.py", "root_id": "root1"}],
            ),
            DbTask(
                kind="upsert_files",
                rows=[_files_row("root1/b.py", "root1")],
                engine_docs=[{"id": "root1/b.py", "root_id": "root1"}],
            ),
        ],
    )
    conn.commit()

    engine = db.engine
    assert len(engine.upserts) == 1
    sent_docs, commit_flag = engine.upserts[0]
    assert commit_flag is False
    assert len(sent_docs) == 2
    assert engine.commits == 1
    db.close_all()


def test_upsert_files_tx_does_not_delete_symbols_when_file_not_updated(tmp_path):
    db = LocalSearchDB(str(tmp_path / "sym.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")
    conn = db._write
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO files(path, rel_path, root_id, repo, mtime, size, content, hash, fts_content, last_seen_ts, deleted_ts, status, error, parse_status, parse_error, ast_status, ast_reason, is_binary, is_minified, metadata_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "root1/a.py",
            "a.py",
            "root1",
            "repo",
            200,
            10,
            b"x",
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
    cur.execute(
        "INSERT INTO symbols(symbol_id, path, root_id, name, kind, line, end_line, content, parent, meta_json, doc_comment, qualname, importance_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sid-a", "root1/a.py", "root1", "A", "class", 1, 2, "class A: pass", "", "{}", "", "A", 1.0),
    )
    conn.commit()

    stale_row = _files_row("root1/a.py", "root1")
    stale_row = list(stale_row)
    stale_row[4] = 100  # older mtime than existing 200
    with db._lock:
        c2 = db._write.cursor()
        db.upsert_files_tx(c2, [tuple(stale_row)])
        db._write.commit()
    left = db._read.execute("SELECT COUNT(1) FROM symbols WHERE path = ?", ("root1/a.py",)).fetchone()[0]
    assert int(left) == 1
    db.close_all()


def test_db_writer_retries_transient_batch_failure(tmp_path):
    class FlakyDB:
        def __init__(self):
            self.engine = None
            self.calls = 0
            self.rows = []

        def upsert_files_tx(self, _cur, rows):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient failure")
            self.rows.extend(rows)

    db = FlakyDB()
    writer = DBWriter(db, max_batch=4, max_wait=0.01)
    writer.start()
    writer.enqueue(DbTask(kind="upsert_files", rows=[_files_row("root1/retry.py", "root1")]))
    assert writer.flush(timeout=2.0) is True
    writer.stop()

    assert db.calls >= 2
    assert len(db.rows) == 1


def test_db_writer_process_batch_without_cursor_rolls_back_on_engine_failure(tmp_path):
    db = LocalSearchDB(str(tmp_path / "roll.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")

    class BadEngine:
        def upsert_documents(self, _docs, commit=True):
            raise RuntimeError("engine fail")

    db.set_engine(BadEngine())
    writer = DBWriter(db)

    with pytest.raises(RuntimeError):
        writer._process_batch(
            None,
            [
                DbTask(
                    kind="upsert_files",
                    rows=[_files_row("root1/atomic.py", "root1")],
                    engine_docs=[{"id": "root1/atomic.py", "root_id": "root1"}],
                )
            ],
        )
    row = db._read.execute("SELECT path FROM files WHERE path = ?", ("root1/atomic.py",)).fetchone()
    assert row is None
    db.close_all()


def test_finalize_turbo_batch_inside_existing_transaction(tmp_path):
    db = LocalSearchDB(str(tmp_path / "nested.db"))
    db.upsert_root("root1", str(tmp_path), str(tmp_path), label="root")
    rows = [_files_row("root1/nested.py", "root1")]
    db.upsert_files_turbo(rows)

    conn = db._write
    cur = conn.cursor()
    cur.execute("BEGIN")
    db.finalize_turbo_batch()
    conn.commit()

    row = db._read.execute("SELECT path FROM files WHERE path = ?", ("root1/nested.py",)).fetchone()
    assert row is not None
    db.close_all()
