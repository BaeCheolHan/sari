import logging

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.indexer import Indexer
from sari.core.workspace import WorkspaceManager
from sari.mcp.tools import registry


def _is_hard_error(response):
    return isinstance(response, dict) and bool(response.get("isError"))


def test_registry_tools_smoke_minimal(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "README.md").write_text("# Hello\nThis is a test.", encoding="utf-8")
    (ws_root / "main.py").write_text("def hello():\n    print('world')\n", encoding="utf-8")

    cfg_dict = Config.get_defaults(str(ws_root))
    cfg_dict["db_path"] = str(ws_root / "index.db")
    cfg = Config(**cfg_dict)
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
        logger=logging.getLogger("tests.registry.smoke"),
        workspace_root=str(ws_root),
        server_version="0.0.0-test",
        policy_engine=None,
    )

    cases = [
        ("status", {}),
        ("list_files", {"limit": 10}),
        ("read_file", {"path": "README.md"}),
        ("search", {"query": "hello"}),
        ("search_symbols", {"query": "hello"}),
        ("repo_candidates", {"query": "test"}),
        ("list_symbols", {"path": "main.py"}),
        ("search_api_endpoints", {"path": "/api"}),
        ("call_graph_health", {}),
        ("save_snippet", {"path": "README.md", "tag": "smoke_test", "start_line": 1, "end_line": 1}),
        ("get_snippet", {"tag": "smoke_test"}),
        ("archive_context", {"topic": "smoke_test", "content": "context content"}),
        ("get_context", {"topic": "smoke_test"}),
        ("dry_run_diff", {"path": "README.md", "content": "# Hello\nThis is a MODIFIED test."}),
    ]

    for name, args in cases:
        res = reg.execute(name, ctx, args)
        assert not _is_hard_error(res), f"{name} returned error: {res}"

    rows = db.get_read_connection().execute("SELECT root_id FROM roots").fetchall()
    root_ids = {row["root_id"] for row in rows}
    assert root_id in root_ids
