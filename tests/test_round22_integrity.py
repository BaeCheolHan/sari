import unittest
import tempfile
import shutil
import sqlite3
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound22Integrity(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "integrity.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_fts_trigger_on_update(self):
        """Verify FTS index updates when main table is updated."""
        path = "update_test.txt"
        self.db.upsert_files([(path, "repo", 0, 0, "initial content", 1000)])
        
        # Search for initial
        hits, _ = self.db.search_v2(SearchOptions(query="initial"))
        self.assertEqual(len(hits), 1)
        
        # Update content
        self.db.upsert_files([(path, "repo", 0, 0, "new shiny content", 2000)])
        
        # Search for old content (should be 0)
        hits_old, _ = self.db.search_v2(SearchOptions(query="initial"))
        self.assertEqual(len(hits_old), 0)
        
        # Search for new content
        hits_new, _ = self.db.search_v2(SearchOptions(query="shiny"))
        self.assertEqual(len(hits_new), 1)

    def test_fts_trigger_on_delete(self):
        """Verify FTS index is cleaned up when file is deleted."""
        path = "delete_test.txt"
        self.db.upsert_files([(path, "repo", 0, 0, "to be deleted", 1000)])
        
        hits, _ = self.db.search_v2(SearchOptions(query="deleted"))
        self.assertEqual(len(hits), 1)
        
        # Delete
        self.db.delete_files([path])
        
        # FTS search should now return 0
        hits_post, _ = self.db.search_v2(SearchOptions(query="deleted"))
        self.assertEqual(len(hits_post), 0)

    def test_large_content_performance(self):
        """Verify snippet generation performance for large files."""
        large_content = ("important_word " + "word " * 100000 + "target ") # Approx 500KB+
        self.db.upsert_files([("large.txt", "repo", 0, 0, large_content, 1000)])
        
        import time
        start = time.time()
        # Search for 'target' at the end of large content
        hits, _ = self.db.search_v2(SearchOptions(query="target"))
        duration = time.time() - start
        
        self.assertEqual(len(hits), 1)
        self.assertLess(duration, 0.5) # Should be fast enough

if __name__ == "__main__":
    unittest.main()
