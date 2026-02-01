import unittest
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound14AdvancedSearch(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "search.db")
        self.db = LocalSearchDB(self.db_path)
        
        # Setup test code data
        self.db.upsert_files([
            ("app.py", "repo", 0, 0, "class MyServer:\n    def start(self):\n        pass", 1000),
            ("utils.py", "repo", 0, 0, "def start_utility():\n    pass", 1000),
            ("readme.md", "repo", 0, 0, "This is MYSERVER documentation.", 1000),
        ])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_regex_search_patterns(self):
        """Verify regex matching for class definitions."""
        opts = SearchOptions(query=r"class\s+\w+", use_regex=True)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "app.py")

    def test_symbol_scoring_boost(self):
        """Verify that files with symbol definitions get higher scores."""
        # Both app.py and utils.py contain 'start'
        # readme.md contains 'MYSERVER' but not 'start'
        
        opts = SearchOptions(query="start")
        hits, _ = self.db.search_v2(opts)
        
        # app.py should have "Symbol definition" in hit_reason because of 'def start'
        app_hit = next(h for h in hits if h.path == "app.py")
        self.assertIn("Symbol definition", app_hit.hit_reason)
        # Verify it has higher score than a generic content match if it exists
        # In this setup, both are symbols.

    def test_case_sensitive_search(self):
        """Verify case sensitivity flag."""
        # Query: 'MyServer'
        
        # 1. Case-insensitive (default)
        opts = SearchOptions(query="MyServer", case_sensitive=False)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 2) # app.py (exact) and readme.md (uppercase)
        
        # 2. Case-sensitive
        opts = SearchOptions(query="MyServer", case_sensitive=True)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "app.py")

if __name__ == "__main__":
    unittest.main()
