import unittest
import tempfile
import os
import shutil
import json
from pathlib import Path
from app.indexer import _redact, Indexer
from app.config import Config
from app.db import LocalSearchDB
from mcp.telemetry import TelemetryLogger

class TestAuditEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.tmp_dir) / "ws"
        self.workspace.mkdir()
        self.db_path = str(self.workspace / "test.db")
        self.db = LocalSearchDB(self.db_path)
        self.log_dir = Path(self.tmp_dir) / "logs"
        self.logger = TelemetryLogger(self.log_dir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_redact_logic(self):
        """Case 1: Redact sensitive info"""
        content = 'password="secret123", token: "api-key-456"'
        redacted = _redact(content)
        self.assertIn('password="***"', redacted)
        self.assertIn('token: "***"', redacted)
        self.assertNotIn("secret123", redacted)

    def test_max_file_bytes(self):
        """Case 2: File size limit"""
        large_file = self.workspace / "large.txt"
        large_file.write_text("a" * 1000)
        
        cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=500, # Limit to 500 bytes
            db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=True, commit_batch_size=500
        )
        
        indexer = Indexer(cfg, self.db)
        indexer._scan_once()
        
        self.assertEqual(self.db.count_files(), 0)

    def test_telemetry_format(self):
        """Case 4: Telemetry log format"""
        self.logger.log_info("test message")
        log_file = self.log_dir / "deckard.log"
        self.assertTrue(log_file.exists())
        content = log_file.read_text()
        self.assertIn("[INFO] test message", content)
        # Check ISO timestamp format roughly [2026-...]
        self.assertTrue(content.startswith("[202"))

    def test_extension_filtering(self):
        """Case 5: Extension filtering"""
        (self.workspace / "keep.py").touch()
        (self.workspace / "skip.exe").touch()
        
        cfg = Config(
            workspace_root=str(self.workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=800000,
            db_path=self.db_path,
            include_ext=[".py"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=True, commit_batch_size=500
        )
        
        indexer = Indexer(cfg, self.db)
        indexer._scan_once()
        
        paths = self.db.get_all_file_paths()
        self.assertIn("keep.py", paths)
        self.assertNotIn("skip.exe", paths)

    def test_config_load_invalid_path(self):
        """Case 3: Config load fallback when path invalid"""
        # Set invalid config env
        os.environ["DECKARD_CONFIG"] = "/non/existent/config.json"
        from app.workspace import WorkspaceManager
        path = WorkspaceManager.resolve_config_path(str(self.workspace))
        self.assertTrue(path.endswith("config.json"))
        self.assertNotIn("non/existent", path)

if __name__ == "__main__":
    unittest.main()
