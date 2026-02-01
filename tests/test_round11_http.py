import unittest
import unittest.mock
import json
import urllib.request
import threading
import time
import tempfile
import shutil
from pathlib import Path
from app.db import LocalSearchDB
from app.http_server import serve_forever
from app.indexer import Indexer
from app.config import Config

class TestRound11HTTP(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "http_test.db")
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("test.txt", "repo", 0, 0, "hello world", 1000)])
        
        # Start server in background thread
        self.port = 49999
        self.host = "127.0.0.1"
        
        # Mock indexer
        self.indexer = unittest.mock.MagicMock()
        self.indexer.status.index_ready = True
        self.indexer.status.last_scan_ts = 0
        self.indexer.status.scanned_files = 0
        self.indexer.status.indexed_files = 0
        self.indexer.status.errors = 0
        
        self.httpd, self.actual_port = serve_forever(self.host, self.port, self.db, self.indexer, version="1.2.3")
        self.base_url = f"http://{self.host}:{self.actual_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_http_status_endpoint(self):
        """Verify /status endpoint returns valid JSON with version."""
        with urllib.request.urlopen(f"{self.base_url}/status") as response:
            self.assertEqual(response.status, 200)
            data = json.loads(response.read().decode())
            self.assertTrue(data["ok"])
            self.assertEqual(data["version"], "1.2.3")

    def test_http_search_api(self):
        """Verify /search endpoint works via GET."""
        # Use a real Config for snippet_max_lines
        self.indexer.cfg.snippet_max_lines = 5
        url = f"{self.base_url}/search?q=hello"
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.status, 200)
            data = json.loads(response.read().decode())
            self.assertTrue(data["ok"])
            self.assertGreater(len(data["hits"]), 0)

    def test_http_search_missing_query(self):
        """Verify /search returns error when 'q' is missing."""
        url = f"{self.base_url}/search"
        try:
            urllib.request.urlopen(url)
            self.fail("Should have raised HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode())
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "missing q")

if __name__ == "__main__":
    unittest.main()
