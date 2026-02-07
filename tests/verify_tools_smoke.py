
import os
import shutil
import tempfile
import json
from pathlib import Path
from sari.core.db import LocalSearchDB
from sari.core.config import Config
from sari.mcp.tools import registry
from sari.core.indexer import Indexer
import logging

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sari.tools.smoke")

def test_tools():
    # 1. Setup Temp Workspace
    tmp_dir = tempfile.mkdtemp()
    try:
        ws_root = Path(tmp_dir)
        (ws_root / "README.md").write_text("# Hello\nThis is a test.")
        (ws_root / "main.py").write_text("def hello():\n    print('world')\n")
        
        # 2. Setup DB & Config
        config = Config.get_defaults(str(ws_root))
        config["db_path"] = str(ws_root / "index.db")
        cfg = Config(**config)
        
        db = LocalSearchDB(cfg.db_path)
        
        
        # 3. Setup Indexer (Mock-ish)
        from sari.core.workspace import WorkspaceManager
        root_id = WorkspaceManager.root_id_for_workspace(str(ws_root))
        db.ensure_root(root_id, str(ws_root))
        
        indexer = Indexer(cfg, db)
        # Scan once to populate DB
        indexer.scan_once()
        
        # 4. Build Registry
        reg = registry.build_default_registry()
        
        # 5. Define Test Cases
        test_cases = [
            ("status", {}),
            ("doctor", {}),
            ("list_files", {"limit": 10}),
            ("read_file", {"path": "README.md"}),
            ("search", {"query": "hello"}),
            ("search_symbols", {"query": "hello"}),
            ("read_symbol", {"name": "hello"}), # might fail if not indexed, but run helper
            ("grep_and_read", {"query": "world"}),
            ("repo_candidates", {"query": "test"}),
            ("list_symbols", {"path": "main.py"}),
            ("search_api_endpoints", {"path": "/api"}),
            ("index_file", {"path": "main.py"}),
            ("get_callers", {"name": "hello"}),
            ("get_implementations", {"name": "hello"}),
            ("call_graph", {"symbol": "hello"}),
            ("call_graph_health", {}),
            ("save_snippet", {"path": "README.md", "tag": "smoke_test", "start_line": 1, "end_line": 1}),
            ("get_snippet", {"tag": "smoke_test"}),
            ("archive_context", {"topic": "smoke_test", "content": "context content"}),
            ("get_context", {"topic": "smoke_test"}),
            ("dry_run_diff", {"path": "README.md", "content": "# Hello\nThis is a MODIFIED test."}),
        ]
        
        ctx = registry.ToolContext(
            db=db,
            engine=getattr(db, "engine", None),
            indexer=indexer,
            roots=[str(ws_root)],
            cfg=cfg,
            logger=logger,
            workspace_root=str(ws_root),
            server_version="0.0.0-test",
            policy_engine=None
        )
        
        results = {}
        for name, args in test_cases:
            print(f"Testing tool: {name}...", end=" ")
            try:
                # Resolve path for file-based args
                if "path" in args and not args["path"].startswith("/"):
                    # For dry_run_diff, path must be absolute?
                    # The tools mostly handle relative paths or resolve against root.
                    # Let's keep them relative as they would be from client.
                    pass
                
                res = reg.execute(name, ctx, args)
                if isinstance(res, dict) and res.get("isError"):
                    print(f"FAILED (Soft): {res}")
                    results[name] = "FAIL_SOFT"
                else:
                    print("PASS")
                    results[name] = "PASS"
            except Exception as e:
                print(f"CRASH: {e}")
                results[name] = f"CRASH: {e}"
                
                results[name] = f"CRASH: {e}"
        
        # Verify Root ID
        from sari.core.workspace import WorkspaceManager
        normalized_ws = WorkspaceManager.normalize_path(str(ws_root))
        
        print("\nVerifying Root ID in DB...")
        import sqlite3
        with sqlite3.connect(cfg.db_path) as conn:
            rows = conn.execute("SELECT root_id, root_path FROM roots").fetchall()
            for row in rows:
                rid = row[0]
                rpath = row[1]
                print(f"Row: id={rid}, path={rpath}")
                if rid == normalized_ws:
                    print(f"PASS: Root ID matches normalized workspace path: {rid}")
                else:
                    print(f"FAIL: Root ID '{rid}' != '{normalized_ws}'")

        print("\nSummary:")
        for name, res in results.items():
            print(f"{name}: {res}")
            
    finally:
        shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    test_tools()
