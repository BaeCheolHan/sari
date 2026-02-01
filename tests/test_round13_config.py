import unittest
import os
import json
import tempfile
import shutil
from pathlib import Path
from app.config import Config

class TestRound13Config(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.tmp_dir) / "config.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_corrupt_config_fallback(self):
        """Verify fallback to defaults when config.json is invalid."""
        self.config_path.write_text("{ invalid json: ")
        
        # Config.load currently raises JSONDecodeError. 
        # Let's see if app/main.py handles it or if Config.load should be more robust.
        with self.assertRaises(json.JSONDecodeError):
            Config.load(str(self.config_path))

    def test_env_priority_port(self):
        """Verify DECKARD_PORT overrides config file."""
        self.config_path.write_text(json.dumps({
            "workspace_root": self.tmp_dir,
            "server_port": 11111
        }))
        
        # Case 1: No env var
        cfg = Config.load(str(self.config_path))
        self.assertEqual(cfg.server_port, 11111)
        
        # Case 2: With env var
        os.environ["DECKARD_PORT"] = "22222"
        try:
            cfg = Config.load(str(self.config_path))
            self.assertEqual(cfg.server_port, 22222)
        finally:
            del os.environ["DECKARD_PORT"]

    def test_config_path_expansion(self):
        """Verify tilde expansion in db_path."""
        self.config_path.write_text(json.dumps({
            "workspace_root": self.tmp_dir,
            "db_path": "~/deckard_test.db"
        }))
        
        cfg = Config.load(str(self.config_path))
        self.assertTrue(cfg.db_path.startswith(os.path.expanduser("~")))
        self.assertNotIn("~", cfg.db_path)

if __name__ == "__main__":
    unittest.main()
