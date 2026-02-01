import unittest
import sqlite3
import tempfile
import shutil
import json
import urllib.request
import time
from pathlib import Path
from app.db import LocalSearchDB
from mcp.telemetry import TelemetryLogger

class TestRound15Maintenance(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_db_migration_safety(self):
        """Verify that missing last_seen column is added automatically."""
        db_path = str(Path(self.tmp_dir) / "old.db")
        
        # 1. Create a legacy table without 'last_seen'
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, repo TEXT, mtime INTEGER, size INTEGER, content TEXT)")
        conn.execute("INSERT INTO files VALUES ('old.txt', 'repo', 100, 10, 'old content')")
        conn.commit()
        conn.close()
        
        # 2. Open with LocalSearchDB (should migrate)
        db = LocalSearchDB(db_path)
        
        # 3. Verify column exists
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(files)")
        columns = [row[1] for row in cursor.fetchall()]
        self.assertIn("last_seen", columns)
        
        # 4. Verify data preserved
        res = conn.execute("SELECT content FROM files WHERE path='old.txt'").fetchone()
        self.assertEqual(res[0], 'old content')
        
        db.close()
        conn.close()

    def test_telemetry_timestamp_format(self):
        """Verify ISO format timestamp in logs."""
        log_dir = Path(self.tmp_dir) / "logs"
        logger = TelemetryLogger(log_dir)
        logger.log_info("test message")
        
        log_file = log_dir / "deckard.log"
        line = log_file.read_text().splitlines()[0]
        
        # Format: [2026-02-01T...] [INFO] test message
        timestamp_part = line[1:].split(']')[0]
        # Should be parseable as ISO
        from datetime import datetime
        try:
            datetime.fromisoformat(timestamp_part)
        except ValueError:
            self.fail(f"Timestamp '{timestamp_part}' is not in valid ISO format")

    def test_http_health_check(self):
        """Verify /health endpoint via direct handler call."""
        from app.http_server import Handler
        from unittest.mock import MagicMock
        import io
        
        # Mock dependencies
        mock_db = MagicMock()
        mock_indexer = MagicMock()
        
        # Setup handler with mocks
        class TestHandler(Handler):
            def __init__(self, *args, **kwargs):
                pass # Skip socket init
            def setup(self): pass
            def handle(self): pass
            def finish(self): pass

        handler = TestHandler()
        handler.db = mock_db
        handler.indexer = mock_indexer
        handler.wfile = io.BytesIO()
        handler.path = "/health"
        
        # Mock base class methods to capture response
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        
        # Execute
        handler.do_GET()
        
        # Verify
        handler.send_response.assert_called_with(200)
        response_body = handler.wfile.getvalue().decode("utf-8")
        data = json.loads(response_body)
        self.assertTrue(data["ok"])

if __name__ == "__main__":
    unittest.main()
