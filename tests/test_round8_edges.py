import unittest
import tempfile
import shutil
import os
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions
from app.indexer import Indexer
from app.config import Config

class TestRound8Edges(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.tmp_dir) / "ws"
        self.workspace.mkdir()
        self.db_path = str(self.workspace / "test.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_unicode_paths(self):
        """Verify indexing and searching for files with Unicode characters."""
        unicode_fn = "테스트_파일_🚀.txt"
        unicode_content = "안녕하세요, 호라드릭 데커드입니다. 🧙‍♂️"
        (self.workspace / unicode_fn).write_text(unicode_content, encoding="utf-8")
        
        cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=1000, db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        
        indexer = Indexer(cfg, self.db)
        indexer._scan_once()
        
        # Search by unicode filename stem
        hits, _ = self.db.search_v2(SearchOptions(query="테스트"))
        self.assertTrue(len(hits) > 0)
        self.assertIn(unicode_fn, hits[0].path)
        
        # Search by unicode content
        hits, _ = self.db.search_v2(SearchOptions(query="데커드"))
        self.assertTrue(len(hits) > 0)
        self.assertIn("🧙‍♂️", hits[0].snippet)

    def test_symlink_safety(self):
        """Verify that indexer does not follow symlinks pointing outside the workspace."""
        external_file = Path(self.tmp_dir) / "external.txt"
        external_file.write_text("external content")
        
        # Create a symlink inside workspace pointing to external file
        link_path = self.workspace / "link_to_external.txt"
        try:
            os.symlink(external_file, link_path)
        except OSError:
            self.skipTest("Symlinks not supported on this platform/user")
            
        cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=1000, db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        
        indexer = Indexer(cfg, self.db)
        indexer._scan_once()
        
        paths = self.db.get_all_file_paths()
        # Indexer uses os.walk which by default does NOT follow symlinks
        self.assertNotIn("link_to_external.txt", paths)

    def test_symbol_token_matching(self):
        """Verify that FTS5 handles symbols like underscores in tokens."""
        self.db.upsert_files([
            ("code.py", "repo", 0, 0, "def my_special_function_name(): pass", 1000)
        ])
        
        # FTS5 default tokenizer might split by underscore. 
        # We need to ensure we can find it by full name or part of it.
        hits, _ = self.db.search_v2(SearchOptions(query="my_special_function_name"))
        self.assertTrue(len(hits) > 0)
        
        hits, _ = self.db.search_v2(SearchOptions(query="special_function"))
        self.assertTrue(len(hits) > 0)

if __name__ == "__main__":
    unittest.main()
