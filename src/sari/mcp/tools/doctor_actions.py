"""Doctor post-check actions and recommendation helpers."""

import os
from typing import TypeAlias

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.workspace import WorkspaceManager

from sari.mcp.tools.doctor_common import result

DoctorResult: TypeAlias = dict[str, object]
DoctorResults: TypeAlias = list[DoctorResult]
ActionItem: TypeAlias = dict[str, str]
ActionItems: TypeAlias = list[ActionItem]


def recommendations(results: DoctorResults) -> ActionItems:
    recs: ActionItems = []
    for r in results:
        if r.get("passed"):
            continue
        name = str(r.get("name") or "")
        if name in {
            "DB Access",
            "DB Integrity",
            "DB Schema",
            "DB Schema Symbol IDs",
            "DB Schema Relations IDs",
            "DB Schema Snippet Anchors",
            "DB Schema Context Validity",
            "DB Schema Snippet Versions",
            "Search Engine Runtime",
            "Embedded Engine Module",
        }:
            recs.append({"name": name, "action": "Upgrade to latest code and run a full rescan."})
        elif name == "Engine Tokenizer Data":
            recs.append({"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "Lindera Dictionary":
            recs.append({"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "CJK Tokenizer Data" or name == "Lindera Engine":
            recs.append({"name": name, "action": "Install CJK support: pip install 'sari[cjk]'"})
        elif name == "Tree-sitter Support":
            recs.append({"name": name, "action": "Install high-precision parsers: pip install 'sari[treesitter]'"})
        elif name.startswith("Daemon Port") or name.startswith("HTTP Port"):
            recs.append({"name": name, "action": "Change port or stop the conflicting process."})
        elif name == "Sari Daemon":
            recs.append({"name": name, "action": "Start the daemon using `sari daemon start`."})
        elif name == "Network Check":
            recs.append({"name": name, "action": "Ensure internet access or use include_network=false if offline."})
        elif name == "Disk Space":
            recs.append({"name": name, "action": "Free up space or move the workspace to a larger volume."})
        elif name == "Search-First Usage":
            recs.append({"name": name, "action": "Enable search-first enforcement or ensure client searches before reading."})
        elif name == "Workspace Overlap":
            recs.append(
                {
                    "name": name,
                    "action": "Remove nested workspaces from MCP settings. Keep only the top-level root or individual project roots.",
                }
            )
        elif name == "Windows Write Lock":
            recs.append({"name": name, "action": "msvcrt.locking is required on Windows. Use a supported Python runtime."})
        elif name == "DB Migration Safety":
            recs.append(
                {
                    "name": name,
                    "action": "Disable destructive migration paths and keep additive schema initialization strategy.",
                }
            )
        elif name == "Engine Sync DLQ":
            recs.append(
                {
                    "name": name,
                    "action": "Run rescan/retry and check engine status until pending sync-error tasks are cleared.",
                }
            )
        elif name == "Writer Health":
            recs.append(
                {
                    "name": name,
                    "action": "Restart daemon if writer thread is dead and check DB/engine logs for the root error.",
                }
            )
        elif name == "Storage Switch Guard":
            recs.append(
                {
                    "name": name,
                    "action": "Restart process to clear blocked storage switch state and check shutdown behavior.",
                }
            )
    return recs


def auto_fixable(results: DoctorResults) -> ActionItems:
    actions: ActionItems = []
    for r in results:
        if r.get("passed"):
            continue
        name = str(r.get("name") or "")
        error = str(r.get("error") or "")

        if name == "DB Schema Symbol IDs":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Relations IDs":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Snippet Anchors":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Context Validity":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "DB Schema Snippet Versions":
            actions.append({"name": name, "action": "db_migrate"})
        elif name == "Sari Daemon" and "stale registry entry" in error:
            actions.append({"name": name, "action": "cleanup_registry_daemons"})
        elif name == "Sari Daemon" and "Version mismatch" in error:
            actions.append({"name": name, "action": "restart_daemon"})

    try:
        from sari.mcp.server_registry import ServerRegistry

        reg = ServerRegistry()
        data = reg.get_registry_snapshot(include_dead=True)
        if not data or data.get("version") != ServerRegistry.VERSION:
            actions.append({"name": "Server Registry", "action": "repair_registry"})
    except Exception:
        actions.append({"name": "Server Registry", "action": "repair_registry"})

    return actions


def run_auto_fixes(ws_root: str, actions: ActionItems) -> DoctorResults:
    if not actions:
        return []
    results: DoctorResults = []

    for action in actions:
        act = action["action"]
        name = action["name"]

        try:
            if act == "db_migrate":
                cfg_path = WorkspaceManager.resolve_config_path(ws_root)
                cfg = Config.load(cfg_path, workspace_root_override=ws_root)
                db = LocalSearchDB(cfg.db_path)
                db.close()
                results.append(result(f"Auto Fix {name}", True, "Schema migration applied"))

            elif act == "cleanup_registry_daemons":
                from sari.mcp.server_registry import ServerRegistry

                reg = ServerRegistry()
                reg.prune_dead()
                results.append(result(f"Auto Fix {name}", True, "Stale daemon registry entries pruned"))

            elif act == "repair_registry":
                from sari.mcp.server_registry import ServerRegistry

                reg = ServerRegistry()
                reg.reset_registry()
                results.append(result(f"Auto Fix {name}", True, "Corrupted registry file reset"))

            elif act == "restart_daemon":
                from sari.mcp.cli.commands.daemon_commands import cmd_daemon_stop

                class Args:
                    daemon_host = ""
                    daemon_port = None

                cmd_daemon_stop(Args())
                results.append(
                    result(
                        f"Auto Fix {name}",
                        True,
                        "Incompatible daemon stopped. It will restart on next CLI use.",
                    )
                )

        except Exception as e:
            results.append(result(f"Auto Fix {name}", False, str(e)))

    return results


def run_rescan(ws_root: str) -> DoctorResults:
    results: DoctorResults = []
    results.append(result("Auto Fix Rescan Start", True, "scan_once starting"))
    try:
        cfg_path = WorkspaceManager.resolve_config_path(ws_root)
        cfg = Config.load(cfg_path, workspace_root_override=ws_root)
        db = LocalSearchDB(cfg.db_path)
        from sari.core.indexer import Indexer

        indexer = Indexer(
            cfg,
            db,
            indexer_mode="leader",
            indexing_enabled=True,
            startup_index_enabled=True,
        )
        indexer.scan_once()
        db.close()
        results.append(result("Auto Fix Rescan", True, "scan_once completed"))
    except Exception as e:
        results.append(result("Auto Fix Rescan", False, str(e)))
    return results


def check_workspace_overlaps(ws_root: str) -> DoctorResults:
    results = []
    try:
        from sari.mcp.server_registry import ServerRegistry

        reg = ServerRegistry()
        data = reg.get_registry_snapshot(include_dead=True)
        workspaces = list(data.get("workspaces", {}).keys())

        current = WorkspaceManager.normalize_path(ws_root)
        overlaps = []
        for ws in workspaces:
            norm_ws = WorkspaceManager.normalize_path(ws)
            if norm_ws == current:
                continue

            if current.startswith(norm_ws + os.sep) or norm_ws.startswith(current + os.sep):
                overlaps.append(norm_ws)

        if overlaps:
            results.append(
                result(
                    "Workspace Overlap",
                    False,
                    f"Nesting detected with: {', '.join(overlaps)}. This leads to duplicate indexing.",
                )
            )
        else:
            results.append(result("Workspace Overlap", True, "No nested roots detected"))
    except Exception as e:
        results.append(result("Workspace Overlap Check", True, f"Skipped: {e}", warn=True))
    return results
