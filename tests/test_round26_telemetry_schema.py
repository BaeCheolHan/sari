import unittest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock
from app.db import LocalSearchDB, SearchOptions
from mcp.tools.search import execute_search

class TestRound26TelemetrySchema(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("main.py", "repo", 0, 0, "def test(): pass", 1000)])
        self.logger = MagicMock()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_search_result_schema_completeness(self):
        """Verify that all expected fields are present in search results."""
        args = {"query": "test"}
        result = execute_search(args, self.db, self.logger)
        data = json.loads(result["content"][0]["text"])
        
        self.assertTrue(len(data["results"]) > 0)
        hit = data["results"][0]
        
        expected_keys = {"repo", "repo_display", "path", "score", "reason", "snippet"}
        for key in expected_keys:
            self.assertIn(key, hit, f"Missing key: {key}")

    def test_telemetry_latency_logging(self):
        """Verify telemetry logger receives latency information."""
        from mcp.telemetry import TelemetryLogger
        log_dir = Path(self.tmp_dir) / "logs"
        logger = TelemetryLogger(log_dir)
        
        # Execute search which triggers telemetry
        args = {"query": "test"}
        execute_search(args, self.db, logger)
        
        log_file = log_dir / "deckard.log"
        self.assertTrue(log_file.exists())
        log_content = log_file.read_text()
        
        # Should contain 'tool=search' and 'latency='
        self.assertIn("tool=search", log_content)
        self.assertIn("latency=", log_content)

    def test_search_options_propagation(self):
        """Verify that total_mode and context_lines are passed to DB."""
        # We need to spy on db.search_v2
        original_search_v2 = self.db.search_v2
        self.db.search_v2 = MagicMock(side_effect=original_search_v2)
        
        args = {"query": "test", "context_lines": 10}
        execute_search(args, self.db, self.logger)
        
        called_opts = self.db.search_v2.call_args[0][0]
        self.assertEqual(called_opts.snippet_lines, 10)

if __name__ == "__main__":
    unittest.main()
