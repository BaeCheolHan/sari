import unittest
import unittest.mock
import tempfile
import shutil
import os
import time
from pathlib import Path
from app.db import LocalSearchDB
from app.indexer import Indexer
from app.config import Config

class TestRound16Ops(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.tmp_dir) / "ws"
        self.workspace.mkdir()
        self.db_path = str(self.workspace / "ops.db")
        self.db = LocalSearchDB(self.db_path)
        
        self.cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=100, db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[".hidden_dir"], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        self.indexer = Indexer(self.cfg, self.db)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_manual_rescan_trigger(self):
        """Verify rescan event is set when requested."""
        self.assertFalse(self.indexer._rescan.is_set())
        self.indexer.request_rescan()
        self.assertTrue(self.indexer._rescan.is_set())

    def test_borderline_file_size(self):
        """Verify file size limits are strictly enforced."""
        # Exact limit (100 bytes)
        f_exact = self.workspace / "exact.txt"
        f_exact.write_text("a" * 100)
        
        # Over limit (101 bytes)
        f_over = self.workspace / "over.txt"
        f_over.write_text("a" * 101)
        
        self.indexer._scan_once()
        paths = self.db.get_all_file_paths()
        
        self.assertIn("exact.txt", paths)
        self.assertNotIn("over.txt", paths)

    def test_directory_traversal_exclusion(self):
        """Verify that excluded directories are not traversed."""
        hidden_dir = self.workspace / ".hidden_dir"
        hidden_dir.mkdir()
        (hidden_dir / "secret.txt").write_text("should not be indexed")
        
        (self.workspace / "visible.txt").write_text("index me")
        
        self.indexer._scan_once()
        paths = self.db.get_all_file_paths()
        
        self.assertIn("visible.txt", paths)
        self.assertNotIn(".hidden_dir/secret.txt", paths)

if __name__ == "__main__":
    unittest.main()
