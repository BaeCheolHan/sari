import unittest
import tempfile
import shutil
import os
from pathlib import Path
from app.db import LocalSearchDB
from app.indexer import Indexer
from app.config import Config

class TestRound27IndexerResilience(unittest.TestCase):
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
            include_ext=[".txt", ".bin"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        self.indexer = Indexer(self.cfg, self.db)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_indexer_encoding_resilience(self):
        """Verify that indexer handles non-UTF8 or binary content safely."""
        # Create a file with invalid UTF-8 bytes
        bin_file = self.workspace / "invalid_utf8.txt"
        with open(bin_file, "wb") as f:
            f.write(b"Normal text " + b"\xff\xfe\xfd" + b" more text")
        
        # Should not crash
        self.indexer._scan_once()
        
        # Verify it was indexed (with errors ignored)
        paths = self.db.get_all_file_paths()
        self.assertIn("invalid_utf8.txt", paths)

    def test_indexer_permission_denied(self):
        """Verify that indexer skips files it cannot read."""
        unreadable = self.workspace / "no_access.txt"
        unreadable.write_text("top secret")
        os.chmod(unreadable, 0o000) # Remove all permissions
        
        try:
            # Should skip and log error, not crash
            self.indexer._scan_once()
            
            # Check if other files are still indexed
            (self.workspace / "readable.txt").write_text("hello")
            self.indexer._scan_once()
            
            paths = self.db.get_all_file_paths()
            self.assertIn("readable.txt", paths)
            self.assertNotIn("no_access.txt", paths)
        finally:
            os.chmod(unreadable, 0o644)

    def test_multi_workspace_root_resolution(self):
        """Verify WorkspaceManager handles different rootUri inputs."""
        from app.workspace import WorkspaceManager
        
        ws1 = Path(self.tmp_dir) / "ws1"
        ws1.mkdir()
        ws2 = Path(self.tmp_dir) / "ws2"
        ws2.mkdir()
        
        res1 = WorkspaceManager.resolve_workspace_root(str(ws1))
        res2 = WorkspaceManager.resolve_workspace_root(str(ws2))
        
        self.assertEqual(Path(res1).resolve(), ws1.resolve())
        self.assertEqual(Path(res2).resolve(), ws2.resolve())

if __name__ == "__main__":
    unittest.main()
