import unittest
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound28Logic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "logic.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_search_hit_reasons_accuracy(self):
        """Verify that multiple match reasons are captured."""
        # 1. Filename match + Tag match
        self.db.upsert_repo_meta("my-repo", tags="logic,test", domain="search")
        self.db.upsert_files([("logic_core.py", "my-repo", 0, 0, "some content", 1000)])
        
        hits, _ = self.db.search_v2(SearchOptions(query="logic"))
        self.assertTrue(len(hits) > 0)
        reason = hits[0].hit_reason
        self.assertIn("Filename match", reason)
        self.assertIn("Tag match", reason)

    def test_common_word_search(self):
        """Verify that FTS5 handles common words gracefully."""
        self.db.upsert_files([("f.txt", "repo", 0, 0, "the quick brown fox", 1000)])
        
        # 'the' is a common stopword in many FTS configurations
        opts = SearchOptions(query="the")
        hits, _ = self.db.search_v2(opts)
        # Should not crash, and should likely find it via LIKE fallback if FTS ignores it
        self.assertIsInstance(hits, list)

    def test_repo_meta_atomicity(self):
        """Verify upsert_repo_meta replaces old data correctly."""
        repo = "test-repo"
        self.db.upsert_repo_meta(repo, tags="old", description="old desc")
        self.db.upsert_repo_meta(repo, tags="new", description="new desc")
        
        meta = self.db.get_repo_meta(repo)
        self.assertEqual(meta["tags"], "new")
        self.assertEqual(meta["description"], "new desc")

if __name__ == "__main__":
    unittest.main()
