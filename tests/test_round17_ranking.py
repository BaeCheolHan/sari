import unittest
import tempfile
import shutil
import time
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestRound17Ranking(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "ranking.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_recency_boost_ranking(self):
        """Older files should be ranked lower when recency_boost is enabled."""
        now = int(time.time())
        # Old file (10 days ago)
        self.db.upsert_files([("old.py", "repo", now - 864000, 100, "target content", now)])
        # New file (just now)
        self.db.upsert_files([("new.py", "repo", now, 100, "target content", now)])
        
        # 1. Without boost (default FTS score/mtime)
        opts = SearchOptions(query="target", recency_boost=False)
        hits, _ = self.db.search_v2(opts)
        # Default sort is score, then mtime DESC. So new.py should be first anyway if scores are same.
        self.assertEqual(hits[0].path, "new.py")

        # 2. With boost
        opts = SearchOptions(query="target", recency_boost=True)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "new.py")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_directory_match_scoring(self):
        """Files in directories matching the query should get higher scores."""
        # Query: 'auth'
        self.db.upsert_files([
            ("common/utils.py", "repo", 0, 0, "authentication logic", 1000),
            ("auth/service.py", "repo", 0, 0, "some code", 1000),
        ])
        
        opts = SearchOptions(query="auth")
        hits, _ = self.db.search_v2(opts)
        
        # auth/service.py matches by directory name -> should be first
        self.assertEqual(hits[0].path, "auth/service.py")
        self.assertIn("Dir match", hits[0].hit_reason)

    def test_multi_term_extraction(self):
        """Verify extraction of multiple terms for snippet highlighting."""
        from app.db import LocalSearchDB
        db = LocalSearchDB(":memory:")
        terms = db._extract_terms("hello world 'quoted term'")
        self.assertIn("hello", terms)
        self.assertIn("world", terms)
        self.assertIn("quoted term", terms)

if __name__ == "__main__":
    unittest.main()
