import argparse
from typing import Callable, List

from sari.entry_commands import run_cmd


def dispatch_pre_stdio(
    argv: List[str],
    run_cmd_fn: Callable[[List[str]], int] | None = None,
) -> int | None:
    """Route CLI-style commands before stdio/http server bootstrap."""
    if not argv:
        return None

    effective_run_cmd = run_cmd_fn or run_cmd

    if argv[0] in {"doctor", "roots", "config", "index", "engine", "uninstall"}:
        return effective_run_cmd(argv)

    if argv[0] in {"daemon", "proxy", "status", "search", "init", "auto"}:
        from sari.mcp.cli import main as mcp_cli_main

        return mcp_cli_main(argv)

    if "--cmd" in argv:
        idx = argv.index("--cmd")
        cmd_args = argv[idx + 1:]
        return effective_run_cmd(cmd_args)

    return None


def _build_transport_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--format", default="pack", choices=["pack", "json"])
    parser.add_argument("--http-api", action="store_true")
    parser.add_argument("--http-api-port")
    parser.add_argument("--http-daemon", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--help", action="store_true")
    return parser


def parse_transport_args(argv: List[str]) -> argparse.Namespace:
    parser = _build_transport_parser()
    ns, _ = parser.parse_known_args(argv)
    return ns
