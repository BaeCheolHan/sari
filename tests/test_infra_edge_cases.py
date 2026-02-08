import pytest
from sari.core.db.main import LocalSearchDB

def test_db_empty_batch_resilience(db):
    """
    Verify that finalize_turbo_batch doesn't crash on an empty staging table.
    """
    # Simply call it without any data
    db.finalize_turbo_batch()
    # If we reached here, it's resilient.

def test_db_malformed_row_isolation(db):
    """
    Verify that one bad row doesn't corrupt the entire DB.
    """
    # Staging table should be isolated
    # Ensure root exists for FK constraint
    db.ensure_root("root", "root")
    bad_row = ("p1",) # Too short, should cause SQL error
    try:
        db.upsert_files_turbo([bad_row])
    except: pass
    
    # DB should still be functional
    db.finalize_turbo_batch()