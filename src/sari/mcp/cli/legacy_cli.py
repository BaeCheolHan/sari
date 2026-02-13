"""Backward-compatibility shim for old CLI module path."""

from .compat_cli import (
    _get_http_host_port,
    cmd_daemon_ensure,
    cmd_daemon_refresh,
    cmd_daemon_start,
    cmd_daemon_status,
    cmd_daemon_stop,
    cmd_doctor,
    cmd_init,
    cmd_prune,
    cmd_search,
    cmd_status,
    cmd_vacuum,
    get_daemon_address,
)

__all__ = [
    "_get_http_host_port",
    "cmd_daemon_ensure",
    "cmd_daemon_refresh",
    "cmd_daemon_start",
    "cmd_daemon_status",
    "cmd_daemon_stop",
    "cmd_doctor",
    "cmd_init",
    "cmd_prune",
    "cmd_search",
    "cmd_status",
    "cmd_vacuum",
    "get_daemon_address",
]
