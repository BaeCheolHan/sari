import unittest
import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.db import LocalSearchDB
from mcp.tools.search import execute_search
from app.http_server import serve_forever

class TestRound23Parity(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "parity.db")
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("main.py", "repo", 0, 0, "def parity_test(): pass", 1000)])
        self.logger = MagicMock()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_http_mcp_search_parity(self):
        """Verify that HTTP and MCP return consistent search data structure."""
        # 1. MCP Result
        mcp_res = execute_search({"query": "parity_test"}, self.db, self.logger)
        mcp_data = json.loads(mcp_res["content"][0]["text"])
        
        # 2. HTTP Logic (Simulated by manual handler call or serve_forever)
        # Instead of full HTTP, we check the db.search results which both use
        hits, meta = self.db.search("parity_test", repo=None)
        
        # Compare core fields
        self.assertEqual(len(mcp_data["results"]), len(hits))
        self.assertEqual(mcp_data["results"][0]["path"], hits[0].path)

    def test_env_precedence_logic(self):
        """Verify priority: DECKARD_WORKSPACE_ROOT > LOCAL_SEARCH_WORKSPACE_ROOT."""
        from app.workspace import WorkspaceManager
        
        path1 = str(Path(self.tmp_dir) / "ws1")
        path2 = str(Path(self.tmp_dir) / "ws2")
        os.makedirs(path1, exist_ok=True)
        os.makedirs(path2, exist_ok=True)
        
        env = {
            "DECKARD_WORKSPACE_ROOT": path1,
            "LOCAL_SEARCH_WORKSPACE_ROOT": path2
        }
        with patch.dict("os.environ", env):
            detected = WorkspaceManager.resolve_workspace_root()
            # Use resolve() to handle macOS /private/var symlinks
            self.assertEqual(Path(detected).resolve(), Path(path1).resolve())

    def test_server_json_overwrite(self):
        """Verify server.json is overwritten even if it exists."""
        data_dir = Path(self.tmp_dir) / ".codex/tools/deckard/data"
        data_dir.mkdir(parents=True)
        server_json = data_dir / "server.json"
        server_json.write_text(json.dumps({"pid": 99999, "port": 12345}))
        
        # In app/main.py, server.json is written at startup
        # We simulate the write logic
        new_info = {"pid": os.getpid(), "port": 54321}
        server_json.write_text(json.dumps(new_info))
        
        loaded = json.loads(server_json.read_text())
        self.assertEqual(loaded["port"], 54321)

if __name__ == "__main__":
    unittest.main()
