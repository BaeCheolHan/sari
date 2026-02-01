import unittest
import json
from unittest.mock import MagicMock
from mcp.tools.search import execute_search
from app.db import LocalSearchDB, SearchOptions

class TestRound9MCP(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock(spec=LocalSearchDB)
        self.logger = MagicMock()
        # Mock index status for approx mode test
        self.db.get_index_status.return_value = {"total_files": 200000}
        self.db.get_repo_stats.return_value = {"repo1": 100000, "repo2": 100000}

    def test_tool_argument_validation(self):
        """Verify tool handles invalid argument types/values gracefully."""
        self.db.search_v2.return_value = ([], {"total": 0})
        
        # limit as string that is not a number
        args = {"query": "test", "limit": "invalid"}
        try:
            result = execute_search(args, self.db, self.logger)
            # Should either work (by casting) or return error, not crash
        except Exception as e:
            self.fail(f"execute_search crashed with invalid limit: {e}")

        # negative offset
        args = {"query": "test", "offset": -10}
        result = execute_search(args, self.db, self.logger)
        # Should be normalized to 0 or handled
        data = json.loads(result["content"][0]["text"])
        self.assertGreaterEqual(data["offset"], 0)

    def test_empty_search_hints(self):
        """Verify hints are provided when no results found."""
        self.db.search_v2.return_value = ([], {"fallback_used": False, "total": 0})
        
        args = {"query": "nonexistent_query_xyz", "repo": "some_repo"}
        result = execute_search(args, self.db, self.logger)
        
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(len(data["results"]), 0)
        self.assertIn("hints", data)
        self.assertTrue(len(data["hints"]) > 0)
        # Hint should suggest removing filters
        self.assertTrue(any("filter" in h.lower() for h in data["hints"]))

    def test_approx_total_logic(self):
        """Verify approx total mode is triggered for large indexes."""
        self.db.search_v2.return_value = ([], {"fallback_used": False, "total": -1, "total_mode": "approx"})
        
        args = {"query": "test"}
        execute_search(args, self.db, self.logger)
        
        # Check if SearchOptions was created with total_mode="approx"
        called_args = self.db.search_v2.call_args[0][0]
        self.assertEqual(called_args.total_mode, "approx")

if __name__ == "__main__":
    unittest.main()
