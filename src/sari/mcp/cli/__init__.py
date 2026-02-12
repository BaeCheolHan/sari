"""
Sari CLI package.

This package contains the modular CLI implementation split from the monolithic cli.py.
"""

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
    from .legacy_cli import cmd_proxy as _cmd_proxy
    return _cmd_proxy(args)


def cmd_auto(args):
    from .legacy_cli import cmd_auto as _cmd_auto
    return _cmd_auto(args)


def main(argv=None):
    from .legacy_cli import main as _legacy_main
    return _legacy_main(argv)

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
