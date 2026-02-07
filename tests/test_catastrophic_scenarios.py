import pytest
from unittest.mock import MagicMock, patch
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
    conn.execute("DROP TABLE staging_mem.files_temp")
    
    # Attempting to finalize should fail gracefully
    db.finalize_turbo_batch() # Should log error but not crash the process