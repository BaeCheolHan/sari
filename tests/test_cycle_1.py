#!/usr/bin/env python3
"""
Test Cycle 1: Core Ranking Edge Cases
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import LocalSearchDB, SearchOptions

class TestCycle1(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_1.db")
        self.db = LocalSearchDB(self.db_path)
        
        # Seed Data
        files = [
            ("src/main.py", "def main(): pass", 100),
            ("src/utility.py", "# Helper", 50),
            ("src/User.java", "class User {}", 80),
            ("node_modules/pkg/index.js", "console.log('ignored')", 200),
            ("readme.md", "# Readme", 100),
        ]
        self.db.upsert_files([
            (f[0], "test_repo", 1000, f[2], f[1], 1000) for f in files
        ])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_case_sensitivity(self):
        """Test 1: Case Sensitive Toggle."""
        # 1. Insensitive (Default)
        opts = SearchOptions(query="user", limit=10, case_sensitive=False)
        hits, _ = self.db.search_v2(opts)
        self.assertTrue(any(h.path == "src/User.java" for h in hits), "Should find 'User' with query 'user'")
        
        # 2. Sensitive
        opts = SearchOptions(query="user", limit=10, case_sensitive=True)
        hits, _ = self.db.search_v2(opts)
        self.assertFalse(any(h.path == "src/User.java" for h in hits), "Should NOT find 'User' with query 'user' in sensitive mode")

    def test_regex_mode(self):
        """Test 2: Regex Mode."""
        # Regex for "class \w+"
        opts = SearchOptions(query="class \w+", limit=10, use_regex=True)
        hits, meta = self.db.search_v2(opts)
        
        self.assertTrue(meta.get("regex_mode"), "Meta should indicate regex mode")
        self.assertTrue(any(h.path == "src/User.java" for h in hits), "Regex match failed")

    def test_file_type_filter(self):
        """Test 3: File Type Filter."""
        opts = SearchOptions(query="main", limit=10, file_types=["py"])
        hits, _ = self.db.search_v2(opts)
        
        self.assertTrue(len(hits) > 0)
        for h in hits:
            self.assertTrue(h.path.endswith(".py"), f"Found non-py file: {h.path}")

    def test_exclude_patterns(self):
        """Test 4: Exclude Patterns."""
        opts = SearchOptions(query="index", limit=10, exclude_patterns=["node_modules/*"])
        hits, _ = self.db.search_v2(opts)
        
        for h in hits:
            self.assertNotIn("node_modules", h.path, "Should exclude node_modules")

    def test_exact_filename_boost(self):
        """Test 5: Exact Filename Boost."""
        # Query "main.py"
        opts = SearchOptions(query="main.py", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0].path, "src/main.py", "Exact filename match should be #1")
        self.assertIn("Exact filename match", hits[0].hit_reason)

if __name__ == "__main__":
    unittest.main()
