import unittest
import tempfile
import shutil
import os
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.db import LocalSearchDB
from mcp.server import LocalSearchMCPServer

class TestRound18Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.tmp_dir) / "ws"
        self.workspace.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_db_busy_timeout_behavior(self):
        """Verify that DB operations honor busy_timeout (implicit check)."""
        db_path = str(self.workspace / "busy.db")
        db = LocalSearchDB(db_path)
        
        # Open another connection and lock the table
        conn2 = sqlite3.connect(db_path)
        conn2.execute("BEGIN EXCLUSIVE")
        
        # Try to upsert in a thread, should wait for busy_timeout
        # We just verify the pragmas are set correctly.
        res = db._read.execute("PRAGMA busy_timeout").fetchone()
        self.assertEqual(res[0], 2000) # 2 seconds default
        
        conn2.rollback()
        conn2.close()
        db.close()

    def test_telemetry_log_dir_creation(self):
        """Verify TelemetryLogger creates log directory if missing."""
        log_dir = Path(self.tmp_dir) / "new_logs"
        from mcp.telemetry import TelemetryLogger
        logger = TelemetryLogger(log_dir)
        logger.log_info("creating dir")
        
        self.assertTrue(log_dir.exists())
        self.assertTrue((log_dir / "deckard.log").exists())

    def test_init_timeout_logic(self):
        """Verify server honors DECKARD_INIT_TIMEOUT env var."""
        server = LocalSearchMCPServer(str(self.workspace))
        
        # Mock indexer to never be ready
        with patch("mcp.server.Indexer") as mock_idx_class, \
             patch.dict("os.environ", {"DECKARD_INIT_TIMEOUT": "0.2"}):
            
            mock_indexer = MagicMock()
            mock_indexer.status.index_ready = False
            mock_idx_class.return_value = mock_indexer
            
            start_ts = time.time()
            # This will call _ensure_initialized which has the timeout loop
            server._ensure_initialized()
            duration = time.time() - start_ts
            
            # Should have waited approx 0.2 seconds
            self.assertGreaterEqual(duration, 0.2)
            self.assertLess(duration, 1.0)

if __name__ == "__main__":
    import time
    unittest.main()
