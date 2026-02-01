import unittest
import tempfile
import shutil
import os
import subprocess
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

class TestRound20Setup(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.install_dir = Path(self.tmp_dir) / "install"
        self.install_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_bootstrap_init_logic(self):
        """Verify that 'init' command creates required codex structure."""
        # We can't easily run the actual shell script here without dependencies,
        # but we can test the 'cmd_init' logic from mcp/cli.py.
        from mcp.cli import cmd_init
        
        workspace = Path(self.tmp_dir) / "my_project"
        workspace.mkdir()
        
        args = MagicMock()
        args.workspace = str(workspace)
        args.force = False
        args.no_marker = False
        
        # We need to mock _package_config_path to point to real repo config
        repo_root = Path(__file__).parent.parent
        with patch("mcp.cli._package_config_path", return_value=repo_root / "config" / "config.json"):
            cmd_init(args)
            
        self.assertTrue((workspace / ".codex-root").exists())
        self.assertTrue((workspace / ".codex/tools/deckard/config/config.json").exists())
        
        # Verify content
        config_data = json.loads((workspace / ".codex/tools/deckard/config/config.json").read_text())
        self.assertEqual(config_data["workspace_root"], str(workspace.resolve()))

    def test_daemon_status_cli_logic(self):
        """Verify CLI status reporting logic."""
        from mcp.cli import cmd_daemon_status
        
        # Case: Daemon not running
        args = MagicMock()
        with patch("mcp.cli.is_daemon_running", return_value=False), \
             patch("mcp.cli.read_pid", return_value=None), \
             patch("builtins.print") as mock_print:
            
            res = cmd_daemon_status(args)
            self.assertEqual(res, 1) # Exit code 1 for stopped
            # Verify status output
            mock_print.assert_any_call("Status: ⚫ Stopped")

    def test_log_file_append_robustness(self):
        """Verify that TelemetryLogger handles high frequency writes."""
        from mcp.telemetry import TelemetryLogger
        log_dir = Path(self.tmp_dir) / "logs"
        logger = TelemetryLogger(log_dir)
        
        for i in range(100):
            logger.log_telemetry(f"stress test entry {i}")
            
        log_file = log_dir / "deckard.log"
        lines = log_file.read_text().splitlines()
        self.assertEqual(len(lines), 100)

if __name__ == "__main__":
    unittest.main()
