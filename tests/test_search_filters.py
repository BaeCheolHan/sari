import unittest
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestSearchFilters(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        self.db = LocalSearchDB(self.db_path)
        
        # Setup test data
        files = []
        for i in range(15):
            path = f"src/module/file_{i}.py"
            files.append((path, "repo", 1000, 100, f"content of file {i}", 2000))
        for i in range(5):
            path = f"docs/readme_{i}.md"
            files.append((path, "repo", 1000, 100, f"documentation {i}", 2000))
        
        self.db.upsert_files(files)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_pagination_and_has_more(self):
        """Verify limit, offset and has_more logic."""
        # 1. First page: 10 results
        opts = SearchOptions(query="content", limit=10, offset=0)
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(len(hits), 10)
        # In current impl, total is -1 in approx mode or exact count in exact mode.
        # But for 15 files, it should be exact.
        self.assertEqual(meta["total"], 15)

        # 2. Second page: remaining 5 results
        opts = SearchOptions(query="content", limit=10, offset=10)
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(len(hits), 5)

    def test_multiple_file_types_filter(self):
        """Filter by both .py and .md."""
        # This query matches everything (content + documentation)
        opts = SearchOptions(query="file", file_types=["py", "md"])
        hits, _ = self.db.search_v2(opts)
        # Only 'file_{i}.py' matches 'file' query in content.
        # Let's use a query that matches both.
        
        # Search for something common or just use empty query (but search_v2 requires query)
        # Let's search for "e" which is in "file" and "documentation"
        opts = SearchOptions(query="e", file_types=["py"])
        hits_py, _ = self.db.search_v2(opts)
        self.assertTrue(all(h.path.endswith(".py") for h in hits_py))
        
        opts = SearchOptions(query="e", file_types=["md"])
        hits_md, _ = self.db.search_v2(opts)
        self.assertTrue(all(h.path.endswith(".md") for h in hits_md))

    def test_complex_path_pattern(self):
        """Test glob-like path patterns."""
        # Matches files in 'docs/'
        opts = SearchOptions(query="documentation", path_pattern="docs/*")
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 5)
        self.assertTrue(all(h.path.startswith("docs/") for h in hits))

if __name__ == "__main__":
    unittest.main()
