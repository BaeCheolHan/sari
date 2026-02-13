import argparse
import json
import os
from typing import List

from sari.entry_commands_doctor import _cmd_doctor
from sari.entry_commands_engine import (
    _cmd_engine_install,
    _cmd_engine_rebuild,
    _cmd_engine_status,
    _cmd_engine_verify,
)
from sari.entry_commands_install import _cmd_install
from sari.entry_commands_roots import (
    _cmd_config_show,
    _cmd_roots_add,
    _cmd_roots_list,
    _cmd_roots_remove,
)


def _cmd_index() -> int:
    try:
        from sari.mcp.cli import _request_http
        _request_http("/rescan", {})
        print(json.dumps({"requested": True}))
        return 0
    except Exception as e:
        print(json.dumps({"requested": False, "error": str(e)}))
        return 1


def run_cmd(argv: List[str]) -> int:
    if not argv:
        print("missing subcommand", file=os.sys.stderr)
        return 2
    if argv[0] in {"status", "search"}:
        from sari.mcp.cli import main as mcp_cli_main
        return mcp_cli_main(argv)
    if argv[0] == "doctor":
        return _cmd_doctor()
    if argv[0] == "config" and len(argv) > 1 and argv[1] == "show":
        return _cmd_config_show()
    if argv[0] == "roots":
        if len(argv) < 2:
            print("roots add|remove|list", file=os.sys.stderr)
            return 2
        if argv[1] == "list":
            return _cmd_roots_list()
        if argv[1] == "add" and len(argv) > 2:
            return _cmd_roots_add(argv[2])
        if argv[1] == "remove" and len(argv) > 2:
            return _cmd_roots_remove(argv[2])
    if argv[0] == "index":
        return _cmd_index()
    if argv[0] == "install":
        parser = argparse.ArgumentParser(prog="sari --cmd install")
        parser.add_argument("--host", required=True, choices=["codex", "gemini", "claude", "cursor"])
        parser.add_argument("--print", action="store_true")
        ns = parser.parse_args(argv[1:])
        return _cmd_install(ns.host, ns.print)
    if argv[0] == "engine":
        if len(argv) < 2:
            print("engine status|install|rebuild|verify", file=os.sys.stderr)
            return 2
        action = argv[1]
        if action == "status":
            return _cmd_engine_status()
        if action == "install":
            return _cmd_engine_install()
        if action == "rebuild":
            return _cmd_engine_rebuild()
        if action == "verify":
            return _cmd_engine_verify()
    if argv[0] == "uninstall":
        from sari import uninstall as uninstall_mod
        return uninstall_mod.main(argv[1:])
    print(f"Unknown subcommand: {argv[0]}", file=os.sys.stderr)
    return 2
