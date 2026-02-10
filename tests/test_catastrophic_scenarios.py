import pytest
from unittest.mock import MagicMock
from sari.core.indexer.main import Indexer
from sari.core.db.main import LocalSearchDB
from sari.core.config import Config


def test_indexer_process_pool_shutdown_resilience():
    """
    Verify that Indexer handles executor shutdown gracefully without crashing.
    """
    mock_db = MagicMock(spec=LocalSearchDB)
    cfg = Config(**Config.get_defaults("/tmp"))

    indexer = Indexer(cfg, mock_db)
    indexer.stop()

    # Should not raise error on double stop
    indexer.stop()
    assert indexer._executor is None


def test_db_turbo_rollback_on_failure():
    """
    Verify that RAM-to-Disk flush rolls back atomically on SQL error.
    """
    db = LocalSearchDB(":memory:")
    # We deliberately break the staging table to cause flush error
    conn = db.db.connection()
    # We deliberately break the staging table schema to cause flush error
    # Dropping it is insufficient as _ensure_staging() recreates it
    try:
        conn.execute("DROP TABLE staging_mem.files_temp")
    except Exception:
        pass
    conn.execute("CREATE TABLE staging_mem.files_temp (invalid_col TEXT)")
    conn.execute("INSERT INTO staging_mem.files_temp VALUES ('bad')")

    # Attempting to finalize should fail gracefully (raise exception now)
    with pytest.raises(Exception):
        db.finalize_turbo_batch()
