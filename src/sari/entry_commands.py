import argparse
import json
import os
from typing import Callable, List

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


def _dispatch_legacy_cli(argv: List[str]) -> int:
    from sari.mcp.cli import main as mcp_cli_main

    return mcp_cli_main(argv)


def _dispatch_doctor(_argv: List[str]) -> int:
    return _cmd_doctor()


def _dispatch_config(argv: List[str]) -> int | None:
    if len(argv) > 1 and argv[1] == "show":
        return _cmd_config_show()
    return None


def _dispatch_roots(argv: List[str]) -> int | None:
    if len(argv) < 2:
        print("roots add|remove|list", file=os.sys.stderr)
        return 2
    action = argv[1]
    if action == "list":
        return _cmd_roots_list()
    if action == "add" and len(argv) > 2:
        return _cmd_roots_add(argv[2])
    if action == "remove" and len(argv) > 2:
        return _cmd_roots_remove(argv[2])
    return None


def _dispatch_index(_argv: List[str]) -> int:
    return _cmd_index()


def _dispatch_install(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="sari --cmd install")
    parser.add_argument("--host", required=True, choices=["codex", "gemini", "claude", "cursor"])
    parser.add_argument("--print", action="store_true")
    ns = parser.parse_args(argv[1:])
    return _cmd_install(ns.host, ns.print)


def _dispatch_engine(argv: List[str]) -> int | None:
    if len(argv) < 2:
        print("engine status|install|rebuild|verify", file=os.sys.stderr)
        return 2
    action = argv[1]
    handlers: dict[str, Callable[[], int]] = {
        "status": _cmd_engine_status,
        "install": _cmd_engine_install,
        "rebuild": _cmd_engine_rebuild,
        "verify": _cmd_engine_verify,
    }
    handler = handlers.get(action)
    if handler is None:
        return None
    return handler()


def _dispatch_uninstall(argv: List[str]) -> int:
    from sari import uninstall as uninstall_mod

    return uninstall_mod.main(argv[1:])


_PRIMARY_DISPATCHERS: dict[str, Callable[[List[str]], int | None]] = {
    "status": _dispatch_legacy_cli,
    "search": _dispatch_legacy_cli,
    "doctor": _dispatch_doctor,
    "config": _dispatch_config,
    "roots": _dispatch_roots,
    "index": _dispatch_index,
    "install": _dispatch_install,
    "engine": _dispatch_engine,
    "uninstall": _dispatch_uninstall,
}


def _resolve_command_handler(argv: List[str]) -> Callable[[List[str]], int | None] | None:
    if not argv:
        return None
    return _PRIMARY_DISPATCHERS.get(argv[0])


def run_cmd(argv: List[str]) -> int:
    if not argv:
        print("missing subcommand", file=os.sys.stderr)
        return 2
    handler = _resolve_command_handler(argv)
    if handler is not None:
        result = handler(argv)
        if result is not None:
            return result
    print(f"Unknown subcommand: {argv[0]}", file=os.sys.stderr)
    return 2
