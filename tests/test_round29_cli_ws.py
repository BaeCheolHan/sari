import unittest
import tempfile
import shutil
import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.workspace import WorkspaceManager

class TestRound29CliWs(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_nested_markers_detection(self):
        """Should detect the closest .codex-root marker."""
        parent = Path(self.tmp_dir) / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        
        (parent / ".codex-root").touch()
        (child / ".codex-root").touch()
        
        old_cwd = os.getcwd()
        os.chdir(child)
        try:
            detected = WorkspaceManager.resolve_workspace_root()
            self.assertEqual(detected, str(child.resolve()))
        finally:
            os.chdir(old_cwd)

    def test_cli_search_command_dispatch(self):
        """Verify CLI search arguments are parsed correctly."""
        from mcp.cli import main
        
        # We mock _request_http to see what's sent
        with patch("mcp.cli._request_http") as mock_req, \
             patch("sys.argv", ["deckard", "search", "my_query", "--limit", "5", "--repo", "my-repo"]):
            
            mock_req.return_value = {"ok": True, "results": []}
            try:
                main()
            except SystemExit:
                pass
            
            # Check the params passed to _request_http
            args, kwargs = mock_req.call_args
            self.assertEqual(args[0], "/search")
            params = args[1]
            self.assertEqual(params["q"], "my_query")
            self.assertEqual(params["limit"], 5)
            self.assertEqual(params["repo"], "my-repo")

    def test_empty_content_search(self):
        """Ensure search doesn't crash on empty/blank files."""
        from app.db import LocalSearchDB, SearchOptions
        db_path = str(Path(self.tmp_dir) / "empty.db")
        db = LocalSearchDB(db_path)
        db.upsert_files([
            ("empty.txt", "repo", 0, 0, "", 1000),
            ("blank.txt", "repo", 0, 0, "   ", 1000),
        ])
        
        # Searching for anything
        hits, _ = db.search_v2(SearchOptions(query="anything"))
        self.assertEqual(len(hits), 0) # Should just return nothing, not crash
        db.close()

if __name__ == "__main__":
    unittest.main()
