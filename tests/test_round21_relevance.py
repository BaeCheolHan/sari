import unittest
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound21Relevance(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "relevance.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_repo_candidates_ranking(self):
        """Verify that repo candidates are ranked by match frequency."""
        self.db.upsert_files([
            ("f1.py", "repo_high", 0, 0, "target target target", 1000),
            ("f2.py", "repo_high", 0, 0, "target", 1000),
            ("f3.py", "repo_low", 0, 0, "target", 1000),
        ])
        
        candidates = self.db.repo_candidates("target", limit=2)
        self.assertEqual(candidates[0]["repo"], "repo_high")
        self.assertGreater(candidates[0]["score"], candidates[1]["score"])

    def test_repo_priority_boost(self):
        """Verify that high-priority repos get a score boost."""
        self.db.upsert_files([
            ("file.py", "repo_normal", 0, 0, "search_term", 1000),
            ("file.py", "repo_priority", 0, 0, "search_term", 1000),
        ])
        
        # Set priority for one repo
        self.db.upsert_repo_meta("repo_priority", priority=100)
        
        hits, _ = self.db.search_v2(SearchOptions(query="search_term"))
        
        # repo_priority should be first due to high priority boost
        self.assertEqual(hits[0].repo, "repo_priority")
        self.assertIn("High priority", hits[0].hit_reason)

    def test_search_options_normalization(self):
        """Ensure SearchOptions handle None/empty inputs gracefully."""
        opts = SearchOptions(query="test", repo="", file_types=None, path_pattern=None)
        # Should not crash and use safe defaults
        hits, meta = self.db.search_v2(opts)
        self.assertIsInstance(hits, list)

if __name__ == "__main__":
    unittest.main()
