import unittest
import tempfile
import shutil
import json
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestSearchRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_fts_special_characters(self):
        """Test search with characters that might break FTS5 logic."""
        self.db.upsert_files([
            ("file.txt", "repo", 0, 0, "Find me with quotes \" and brackets [].", 1000)
        ])
        
        # Mismatched quotes, colons, etc.
        queries = ["\"", "match:", "AND", "OR", "(", ")", "*", "  "]
        for q in queries:
            opts = SearchOptions(query=q)
            # Should not raise exception
            hits, meta = self.db.search_v2(opts)
            self.assertIsInstance(hits, list)

    def test_empty_query(self):
        opts = SearchOptions(query="")
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(len(hits), 0)

if __name__ == "__main__":
    unittest.main()

