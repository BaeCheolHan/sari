import sqlite3
import unittest
import tempfile
import os
import time
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions

class TestDBOptimizations(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")

    def tearDown(self):
        if hasattr(self, 'db'):
            self.db.close()
        import shutil
        shutil.rmtree(self.tmp_dir)

    def test_migration_last_seen(self):
        """Case 1: Migrate existing DB without last_seen"""
        # Create legacy DB
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, repo TEXT, mtime INTEGER, size INTEGER, content TEXT)")
        conn.close()
        
        # Init DB with new code
        self.db = LocalSearchDB(self.db_path)
        
        # Verify last_seen exists
        cur = self.db._read.cursor()
        cur.execute("PRAGMA table_info(files)")
        columns = [row[1] for row in cur.fetchall()]
        self.assertIn("last_seen", columns)

    def test_delete_unseen_files(self):
        """Case 2: Delete unseen files"""
        self.db = LocalSearchDB(self.db_path)
        old_ts = int(time.time()) - 100
        new_ts = int(time.time())
        
        self.db.upsert_files([
            ("seen.txt", "repo1", 0, 0, "content", new_ts),
            ("unseen.txt", "repo1", 0, 0, "content", old_ts)
        ])
        
        count = self.db.delete_unseen_files(new_ts)
        self.assertEqual(count, 1)
        
        paths = self.db.get_all_file_paths()
        self.assertIn("seen.txt", paths)
        self.assertNotIn("unseen.txt", paths)

    def test_approx_search_mode(self):
        """Case 4: approx mode omits COUNT"""
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("f.txt", "r", 0, 0, "hello world", int(time.time()))])
        
        opts = SearchOptions(query="hello", total_mode="approx")
        hits, meta = self.db.search_v2(opts)
        
        self.assertEqual(meta["total"], -1)
        self.assertEqual(meta["total_mode"], "approx")

    def test_exact_search_mode(self):
        """Case 5: exact mode includes COUNT"""
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("f.txt", "r", 0, 0, "hello world", int(time.time()))])
        
        opts = SearchOptions(query="hello", total_mode="exact")
        hits, meta = self.db.search_v2(opts)
        
        self.assertEqual(meta["total"], 1)
        self.assertEqual(meta["total_mode"], "exact")

    def test_upsert_updates_last_seen(self):
        """Case 6: upsert updates last_seen column"""
        self.db = LocalSearchDB(self.db_path)
        path = "update.txt"
        old_ts = 1000
        new_ts = 2000
        
        # Initial upsert
        self.db.upsert_files([(path, "r", 0, 0, "content", old_ts)])
        
        # Update upsert
        self.db.upsert_files([(path, "r", 0, 0, "new content", new_ts)])
        
        with self.db._read_lock:
            row = self.db._read.execute("SELECT last_seen FROM files WHERE path=?", (path,)).fetchone()
        self.assertEqual(row["last_seen"], new_ts)

if __name__ == "__main__":
    unittest.main()
