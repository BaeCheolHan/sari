import pytest
from sari.core.models import IndexingResult

def test_db_empty_batch_resilience(db):
    """
    Verify that finalize_turbo_batch doesn't crash on an empty staging table.
    """
    before = db.db.connection().execute("SELECT COUNT(*) FROM files").fetchone()
    before_count = int(next(iter(before))) if before else 0
    db.finalize_turbo_batch()
    after = db.db.connection().execute("SELECT COUNT(*) FROM files").fetchone()
    after_count = int(next(iter(after))) if after else 0
    assert after_count == before_count

def test_db_malformed_row_isolation(db):
    """
    Verify that one bad row doesn't corrupt the entire DB.
    """
    db.ensure_root("root", "root")
    bad_row = ("p1",)
    try:
        db.upsert_files_turbo([bad_row])
    except Exception:
        pass

    db.finalize_turbo_batch()
    good = IndexingResult(
        path="root/app.py",
        rel="app.py",
        root_id="root",
        repo="repo",
        type="new",
        content="print('ok')",
        fts_content="print ok",
        content_hash="h",
        mtime=1,
        size=10,
        scan_ts=1,
        metadata_json="{}",
    )
    db.upsert_files_turbo([good])
    db.finalize_turbo_batch()
    row = db.db.connection().execute("SELECT COUNT(*) FROM files WHERE path = ?", ("root/app.py",)).fetchone()
    good_count = int(next(iter(row))) if row else 0
    assert good_count == 1
