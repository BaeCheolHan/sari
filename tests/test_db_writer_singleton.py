import tempfile
import time

from sari.core.db import LocalSearchDB
from sari.core.indexer import DBWriter


def _wait_for_writer(db: LocalSearchDB, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if db._writer_thread_id is not None:
            return
        time.sleep(0.01)
    raise AssertionError("writer thread did not register in time")


def test_single_writer_enforced() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = f"{td}/index.db"
        db = LocalSearchDB(db_path)
        writer = DBWriter(db)
        writer.start()
        _wait_for_writer(db)

        try:
            raised = False
            try:
                db.upsert_files([("p", "r", 1, 1, "x", 1)])
            except RuntimeError:
                raised = True
            assert raised, "write outside writer thread should fail"
        finally:
            writer.stop()
            db.close()
