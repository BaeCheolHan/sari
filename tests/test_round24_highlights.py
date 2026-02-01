import unittest
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound24Highlights(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "highlights.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_list_files_hidden_filter(self):
        """Verify that hidden files are included only when requested."""
        self.db.upsert_files([
            ("normal.py", "repo", 0, 0, "content", 1000),
            (".hidden.py", "repo", 0, 0, "secret", 1000),
            ("dir/.secret.py", "repo", 0, 0, "nested secret", 1000),
        ])
        
        # 1. Default (hidden excluded)
        files, _ = self.db.list_files(include_hidden=False)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["path"], "normal.py")
        
        # 2. Include hidden
        files_all, _ = self.db.list_files(include_hidden=True)
        self.assertEqual(len(files_all), 3)

    def test_search_total_count_exact(self):
        """Verify total count in exact mode."""
        self.db.upsert_files([
            (f"file_{i}.txt", "repo", 0, 0, "match_me", 1000) for i in range(50)
        ])
        
        opts = SearchOptions(query="match_me", limit=10, total_mode="exact")
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(meta["total"], 50)

    def test_highlight_boundary_cases(self):
        """Verify highlighting when search term is at the very beginning or end."""
        content = "START word word END"
        self.db.upsert_files([("boundary.txt", "repo", 0, 0, content, 1000)])
        
        # 1. Start
        hits, _ = self.db.search_v2(SearchOptions(query="START"))
        self.assertIn(">>>START<<<", hits[0].snippet)
        
        # 2. End
        hits, _ = self.db.search_v2(SearchOptions(query="END"))
        self.assertIn(">>>END<<<", hits[0].snippet)

if __name__ == "__main__":
    unittest.main()
