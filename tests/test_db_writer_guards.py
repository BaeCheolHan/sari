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
