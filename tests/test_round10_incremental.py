import unittest
import unittest.mock
import tempfile
import shutil
import os
import time
from pathlib import Path
from app.db import LocalSearchDB
from app.indexer import Indexer, AI_SAFETY_NET_SECONDS
from app.config import Config

class TestRound10Incremental(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.tmp_dir) / "ws"
        self.workspace.mkdir()
        self.db_path = str(self.workspace / "test.db")
        self.db = LocalSearchDB(self.db_path)
        
        self.cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=1000, db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        self.indexer = Indexer(self.cfg, self.db)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_incremental_scan_logic(self):
        """Verify that unchanged files are not re-indexed after safety net window."""
        f1 = self.workspace / "file1.txt"
        f1.write_text("content 1")
        f2 = self.workspace / "file2.txt"
        f2.write_text("content 2")
        
        # Initial scan
        self.indexer._scan_once()
        self.assertEqual(self.db.count_files(), 2)
        
        # Wait for safety net to pass
        with unittest.mock.patch("app.indexer.AI_SAFETY_NET_SECONDS", 0):
            # Modify f1, keep f2
            time.sleep(0.1)
            f1.write_text("updated content 1")
            
            # 2nd scan
            self.indexer._scan_once()
            self.assertEqual(self.indexer.status.scanned_files, 2)
            # Only f1 should be re-indexed
            self.assertEqual(self.indexer.status.indexed_files, 1)

    def test_cleanup_deleted_files(self):
        """Verify that deleted files are removed from DB after scan."""
        f1 = self.workspace / "to_delete.txt"
        f1.write_text("bye bye")
        
        self.indexer._scan_once()
        self.assertIn("to_delete.txt", self.db.get_all_file_paths())
        
        # Delete from disk
        f1.unlink()
        
        # Scan again - we need to ensure the scan_ts is strictly greater 
        # than previous last_seen, or just run it. 
        # The indexer uses current time as scan_ts.
        time.sleep(1.1)
        self.indexer._scan_once()
        self.assertNotIn("to_delete.txt", self.db.get_all_file_paths())
        self.assertEqual(self.db.count_files(), 0)

    def test_reindex_on_size_change(self):
        """Verify re-indexing if size changes even if mtime is same."""
        f1 = self.workspace / "size_test.txt"
        f1.write_text("small")
        st = f1.stat()
        mtime, size = int(st.st_mtime), st.st_size
        
        self.indexer._scan_once()
        
        # Mock DB meta to return same mtime but different size
        with unittest.mock.patch.object(self.db, 'get_file_meta', return_value=(mtime, size + 100)), \
             unittest.mock.patch("app.indexer.AI_SAFETY_NET_SECONDS", 0):
            self.indexer._scan_once()
            self.assertEqual(self.indexer.status.indexed_files, 1)

if __name__ == "__main__":
    unittest.main()
