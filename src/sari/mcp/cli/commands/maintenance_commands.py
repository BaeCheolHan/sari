"""
Maintenance command handlers extracted from legacy_cli.
"""

import json
from pathlib import Path

from sari.core.workspace import WorkspaceManager
from sari.core.config import Config

from ..utils import get_arg, load_local_db


def cmd_doctor(args):
    from sari.mcp.tools.doctor import execute_doctor

    payload = execute_doctor(
        {
            "auto_fix": bool(get_arg(args, "auto_fix")),
            "auto_fix_rescan": bool(get_arg(args, "auto_fix_rescan")),
            "include_network": not get_arg(args, "no_network"),
            "include_db": not get_arg(args, "no_db"),
            "include_port": not get_arg(args, "no_port"),
            "include_disk": not get_arg(args, "no_disk"),
            "min_disk_gb": float(get_arg(args, "min_disk_gb", 1.0)),
        }
    )
    print(payload.get("content", [{}])[0].get("text", ""))
    return 0


def cmd_init(args):
    ws_root = Path(get_arg(args, "workspace") or WorkspaceManager.resolve_workspace_root()).expanduser().resolve()
    cfg_path = Path(WorkspaceManager.resolve_config_path(str(ws_root)))
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(cfg_path.read_text()) if cfg_path.exists() and not get_arg(args, "force") else {}
    roots = list(dict.fromkeys((data.get("roots") or []) + [str(ws_root)]))
    data.update({"roots": roots, "db_path": data.get("db_path", Config.get_defaults(str(ws_root))["db_path"])})
    cfg_path.write_text(json.dumps(data, indent=2))
    print(f"✅ Workspace initialized at {ws_root}")
    return 0


def cmd_prune(args):
    db, _, _ = load_local_db(get_arg(args, "workspace"))
    try:
        tables = [get_arg(args, "table")] if get_arg(args, "table") else ["snippets", "failed_tasks", "contexts"]
        for t in tables:
            count = db.prune_data(t, get_arg(args, "days") or 30)
            if count > 0:
                print(f"🧹 {t}: Removed {count} records.")
        return 0
    finally:
        db.close()
