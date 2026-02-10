import argparse
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.indexer import Indexer
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools import registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sari.tools.smoke")


def run_tools_smoke(workspace: str, limit: int) -> Dict[str, Any]:
    created_temp = not bool(workspace)
    tmp_dir = tempfile.mkdtemp(prefix="sari_tools_smoke_") if created_temp else ""
    ws_root = Path(tmp_dir) if created_temp else Path(workspace).resolve()
    if not created_temp and ws_root.exists():
        shutil.rmtree(ws_root)
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "README.md").write_text("# Hello\nThis is a test.", encoding="utf-8")
    (ws_root / "main.py").write_text("def hello():\n    print('world')\n", encoding="utf-8")

    config = Config.get_defaults(str(ws_root))
    config["db_path"] = str(ws_root / "index.db")
    cfg = Config(**config)
    db = LocalSearchDB(cfg.db_path)

    root_id = WorkspaceManager.root_id_for_workspace(str(ws_root))
    db.ensure_root(root_id, str(ws_root))
    indexer = Indexer(cfg, db)
    indexer.scan_once()

    reg = registry.build_default_registry()
    ctx = registry.ToolContext(
        db=db,
        engine=getattr(db, "engine", None),
        indexer=indexer,
        roots=[str(ws_root)],
        cfg=cfg,
        logger=logger,
        workspace_root=str(ws_root),
        server_version="0.0.0-manual",
        policy_engine=None,
    )

    test_cases: List[Tuple[str, Dict[str, Any]]] = [
        ("status", {}),
        ("doctor", {}),
        ("list_files", {"limit": 10}),
        ("read_file", {"path": "README.md"}),
        ("search", {"query": "hello"}),
        ("search_symbols", {"query": "hello"}),
        ("read_symbol", {"name": "hello"}),
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
    ][: max(1, int(limit))]

    results: Dict[str, str] = {}
    for name, args in test_cases:
        try:
            res = reg.execute(name, ctx, args)
            results[name] = "FAIL_SOFT" if isinstance(res, dict) and res.get("isError") else "PASS"
        except Exception as exc:
            results[name] = f"CRASH: {exc}"

    rows = db.get_read_connection().execute("SELECT root_id, root_path FROM roots").fetchall()
    normalized_ws = WorkspaceManager.normalize_path(str(ws_root))
    root_ok = any(row["root_id"] == normalized_ws for row in rows)

    if created_temp and Path(tmp_dir).exists():
        shutil.rmtree(tmp_dir)

    details = {
        "workspace": str(ws_root),
        "tools_checked": len(test_cases),
        "results": results,
        "root_id_matches_workspace": root_ok,
        "crashes": [k for k, v in results.items() if v.startswith("CRASH:")],
        "soft_failures": [k for k, v in results.items() if v == "FAIL_SOFT"],
    }
    if details["crashes"]:
        status = "fail"
    elif details["soft_failures"]:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "summary": {
            "workspace": details["workspace"],
            "tools_checked": details["tools_checked"],
            "root_id_matches_workspace": details["root_id_matches_workspace"],
            "crash_count": len(details["crashes"]),
            "soft_failure_count": len(details["soft_failures"]),
        },
        "details": details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run manual MCP tools smoke checks.")
    parser.add_argument("--workspace", default="", help="Optional workspace path. Uses temp workspace by default.")
    parser.add_argument("--limit", type=int, default=21, help="Number of tool test cases to run.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_tools_smoke(args.workspace, max(1, int(args.limit)))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        details = result["details"]
        print(f"[ToolsSmoke] status={result['status']} workspace={summary['workspace']} tools_checked={summary['tools_checked']}")
        print(f"[ToolsSmoke] root_id_matches_workspace={summary['root_id_matches_workspace']}")
        print(f"[ToolsSmoke] soft_failures={details['soft_failures']}")
        print(f"[ToolsSmoke] crashes={details['crashes']}")
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
