"""
Sari CLI package.

This package contains the modular CLI implementation split from the monolithic cli.py.
"""

import argparse

from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address

# Re-export key utilities for backward compatibility
from .daemon import (
    is_daemon_running,
    read_pid,
    remove_pid,
    ensure_daemon_running,
    set_daemon_start_function,
)
from .http_client import (
    get_http_host_port,
    request_http,
    is_http_running,
)
from .mcp_client import (
    identify_sari_daemon,
    probe_sari_daemon,
    request_mcp_status,
    ensure_workspace_http,
)
from .utils import (
    get_arg,
    load_config,
    load_local_db,
    is_loopback,
    enforce_loopback,
    get_local_version,
    is_port_in_use,
    DEFAULT_HOST,
    DEFAULT_PORT,
    PID_FILE,
)
from .registry import load_registry_instances, load_server_info

# Import modularized command handlers directly to avoid eager legacy_cli coupling.
from .commands.daemon_commands import (
    cmd_daemon_start,
    cmd_daemon_stop,
    cmd_daemon_status,
    cmd_daemon_ensure,
    cmd_daemon_refresh,
)
from .commands.status_commands import (
    cmd_status,
    cmd_search,
)
from .commands.maintenance_commands import (
    cmd_doctor,
    cmd_init,
    cmd_prune,
    cmd_vacuum,
)


def cmd_proxy(args):
    from .compat_cli import cmd_proxy as _cmd_proxy
    return _cmd_proxy(args)


def cmd_auto(args):
    from .compat_cli import cmd_auto as _cmd_auto
    return _cmd_auto(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sari",
        description="Sari CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    d_parser = subparsers.add_parser("daemon", help="Daemon").add_subparsers(dest="daemon_command")
    start = d_parser.add_parser("start", help="Start")
    start.add_argument("-d", "--daemonize", action="store_true")
    start.add_argument("--daemon-host", default="")
    start.add_argument("--daemon-port", type=int)
    start.set_defaults(func=cmd_daemon_start)

    stop = d_parser.add_parser("stop", help="Stop")
    stop.add_argument("--daemon-host", default="")
    stop.add_argument("--daemon-port", type=int)
    stop.add_argument("--all", action="store_true")
    stop.set_defaults(func=cmd_daemon_stop)

    status = d_parser.add_parser("status", help="Status")
    status.add_argument("--daemon-host", default="")
    status.add_argument("--daemon-port", type=int)
    status.set_defaults(func=cmd_daemon_status)

    ensure = d_parser.add_parser("ensure", help="Ensure")
    ensure.add_argument("--daemon-host", default="")
    ensure.add_argument("--daemon-port", type=int)
    ensure.set_defaults(func=cmd_daemon_ensure)

    refresh = d_parser.add_parser("refresh", help="Refresh")
    refresh.add_argument("--daemon-host", default="")
    refresh.add_argument("--daemon-port", type=int)
    refresh.set_defaults(func=cmd_daemon_refresh)

    subparsers.add_parser("proxy", help="Proxy").set_defaults(func=cmd_proxy)
    subparsers.add_parser("auto", help="Auto").set_defaults(func=cmd_auto)

    st = subparsers.add_parser("status", help="HTTP Status")
    st.add_argument("--daemon-host", default="")
    st.add_argument("--daemon-port", type=int)
    st.add_argument("--http-host", default="")
    st.add_argument("--http-port", type=int)
    st.set_defaults(func=cmd_status)

    search = subparsers.add_parser("search", help="HTTP Search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--repo", default=None)
    search.set_defaults(func=cmd_search)

    doc = subparsers.add_parser("doctor", help="Doctor")
    doc.add_argument("--auto-fix", action="store_true")
    doc.add_argument("--auto-fix_rescan", action="store_true")
    doc.add_argument("--no-network", action="store_true")
    doc.add_argument("--no-db", action="store_true")
    doc.add_argument("--no-port", action="store_true")
    doc.add_argument("--no-disk", action="store_true")
    doc.add_argument("--min-disk-gb", type=float, default=1.0)
    doc.set_defaults(func=cmd_doctor)

    init = subparsers.add_parser("init", help="Init")
    init.add_argument("--workspace", default="")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    prune = subparsers.add_parser("prune", help="Prune")
    prune.add_argument("--days", type=int)
    prune.add_argument("--table", choices=["snippets", "failed_tasks", "contexts"])
    prune.add_argument("--workspace", default="")
    prune.set_defaults(func=cmd_prune)

    vacuum = subparsers.add_parser("vacuum", help="VACUUM sqlite database")
    vacuum.add_argument("--workspace", default="")
    vacuum.set_defaults(func=cmd_vacuum)

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args) if hasattr(args, "func") else 0

# Aliases for backward compatibility with underscore-prefixed names
_get_http_host_port = get_http_host_port
_is_http_running = is_http_running
_identify_sari_daemon = identify_sari_daemon
_request_http = request_http
_request_mcp_status = request_mcp_status


__all__ = [
    # Daemon management
    "is_daemon_running",
    "read_pid",
    "remove_pid",
    "ensure_daemon_running",
    "set_daemon_start_function",
    "get_daemon_address",
    # HTTP client
    "get_http_host_port",
    "request_http",
    "is_http_running",
    # MCP client
    "identify_sari_daemon",
    "probe_sari_daemon",
    "request_mcp_status",
    "ensure_workspace_http",
    # Utils
    "get_arg",
    "load_config",
    "load_local_db",
    "is_loopback",
    "enforce_loopback",
    "get_local_version",
    "is_port_in_use",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PID_FILE",
    # Registry
    "load_registry_instances",
    "load_server_info",
    # Commands from legacy_cli
    "cmd_daemon_start",
    "cmd_daemon_stop",
    "cmd_daemon_status",
    "cmd_daemon_ensure",
    "cmd_daemon_refresh",
    "cmd_proxy",
    "cmd_auto",
    "cmd_status",
    "cmd_search",
    "cmd_doctor",
    "cmd_init",
    "cmd_prune",
    "cmd_vacuum",
    "main",
    # Backward compat aliases
    "_get_http_host_port",
    "_is_http_running",
    "_identify_sari_daemon",
    "_request_http",
    "_request_mcp_status",
]
