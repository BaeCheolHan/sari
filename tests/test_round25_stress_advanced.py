import unittest
import tempfile
import shutil
import threading
import time
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions
from app.indexer import Indexer
from app.config import Config

class TestRound25StressAdvanced(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "stress_v2.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_concurrent_write_read(self):
        """Verify reliability during simultaneous index and search."""
        stop_evt = threading.Event()
        
        def indexer_task():
            count = 0
            while not stop_evt.is_set():
                rows = [(f"file_{count}_{i}.txt", "repo", 0, 0, "content", 1000) for i in range(10)]
                self.db.upsert_files(rows)
                count += 1
                time.sleep(0.01)

        def searcher_task():
            while not stop_evt.is_set():
                opts = SearchOptions(query="content", limit=5)
                try:
                    self.db.search_v2(opts)
                except Exception as e:
                    self.fail(f"Search failed during concurrent write: {e}")
                time.sleep(0.01)

        t1 = threading.Thread(target=indexer_task)
        t2 = threading.Thread(target=searcher_task)
        t1.start()
        t2.start()
        
        time.sleep(0.5)
        stop_evt.set()
        t1.join()
        t2.join()

    def test_db_integrity_check(self):
        """Perform a low-level SQLite integrity check."""
        self.db.upsert_files([("f.txt", "repo", 0, 0, "content", 1000)])
        
        with self.db._read_lock:
            res = self.db._read.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(res[0].lower(), "ok")

    def test_telemetry_latency_logic(self):
        """Verify that telemetry log includes expected latency pattern."""
        from mcp.telemetry import TelemetryLogger
        log_dir = Path(self.tmp_dir) / "telemetry"
        logger = TelemetryLogger(log_dir)
        
        # We simulate a search telemetry entry
        logger.log_telemetry("tool=search latency=123ms")
        
        log_file = log_dir / "deckard.log"
        content = log_file.read_text()
        self.assertIn("latency=123ms", content)

if __name__ == "__main__":
    unittest.main()
