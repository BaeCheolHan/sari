"""
Daemon command handlers extracted from legacy_cli.
"""

import argparse

from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address

from ..daemon_lifecycle_lock import run_with_lifecycle_lock
from ..daemon import is_daemon_running
from ..mcp_client import identify_sari_daemon, ensure_workspace_http
from ..smart_daemon import ensure_smart_daemon


def _arg(args: object, name: str, default: object = None) -> object:
    return getattr(args, name, default) if hasattr(args, name) else default


def _ensure_daemon_running(h: str, p: int):
    res_h, res_p = ensure_smart_daemon(h, p)
    return res_h, res_p, True


def _cmd_daemon_start_impl(args):
    from ..daemon import (
        extract_daemon_start_params,
        handle_existing_daemon,
        check_port_availability,
        prepare_daemon_environment,
        start_daemon_in_background,
        start_daemon_in_foreground,
    )

    params = extract_daemon_start_params(args)
    if (res := handle_existing_daemon(params)) is not None:
        return res
    if (res := check_port_availability(params)) is not None:
        return res
    prepare_daemon_environment(params)
    if _arg(args, "daemonize"):
        return start_daemon_in_background(params)
    return start_daemon_in_foreground(params)


def cmd_daemon_start(args):
    # Foreground start is a long-lived process; holding lifecycle lock for its
    # whole lifetime blocks stop/refresh from other terminals.
    if _arg(args, "daemonize"):
        return run_with_lifecycle_lock("start", lambda: _cmd_daemon_start_impl(args))
    return _cmd_daemon_start_impl(args)


def _cmd_daemon_stop_impl(args):
    from ..daemon import extract_daemon_stop_params, stop_daemon_process

    return stop_daemon_process(extract_daemon_stop_params(args))


def cmd_daemon_stop(args):
    return run_with_lifecycle_lock("stop", lambda: _cmd_daemon_stop_impl(args))


def cmd_daemon_status(args):
    explicit = bool(_arg(args, "daemon_host") or _arg(args, "daemon_port"))
    if explicit:
        host = _arg(args, "daemon_host") or DEFAULT_DAEMON_HOST
        port = int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)
        running = is_daemon_running(host, port)
        identity = identify_sari_daemon(host, port) if running else None
        print(f"Host: {host}\nPort: {port}\nStatus: {'🟢 Running' if running else '⚫ Stopped'}")
        if identity and (root := identity.get("workspaceRoot")):
            print(f"Workspace Root: {root}")
        if identity and (pid := identity.get("pid")):
            print(f"PID: {pid}")
        return 0 if running else 1

    from ..daemon import list_registry_daemons

    active_host, active_port = get_daemon_address()
    daemons = list_registry_daemons()
    print(f"Resolved Target: {active_host}:{active_port}")
    if not daemons:
        print("Status: ⚫ Stopped")
        return 1

    print(f"Status: 🟢 Running ({len(daemons)} instance(s))")
    for d in daemons:
        host = str(d.get("host") or DEFAULT_DAEMON_HOST)
        port = int(d.get("port") or 0)
        pid = int(d.get("pid") or 0)
        ver = str(d.get("version") or "")
        marker = "*" if (host == active_host and port == active_port) else "-"
        print(f"{marker} {host}:{port} PID={pid} VERSION={ver}")
    return 0


def cmd_daemon_ensure(args):
    if _arg(args, "daemon_host") or _arg(args, "daemon_port"):
        host = _arg(args, "daemon_host") or DEFAULT_DAEMON_HOST
        port = int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)
    else:
        host, port = get_daemon_address()

    h, p, _ = _ensure_daemon_running(host, port)
    if is_daemon_running(h, p):
        if ensure_workspace_http(h, p):
            return 0
    print("❌ Failed to ensure daemon services.")
    return 1


def cmd_daemon_refresh(args):
    def _action() -> int:
        if _arg(args, "daemon_host") or _arg(args, "daemon_port"):
            target_host = _arg(args, "daemon_host") or DEFAULT_DAEMON_HOST
            target_port = int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)
        else:
            target_host, target_port = get_daemon_address()

        stop_args = argparse.Namespace(
            daemon_host=target_host,
            daemon_port=target_port,
        )
        stop_rc = _cmd_daemon_stop_impl(stop_args)
        if stop_rc != 0:
            return stop_rc

        start_args = argparse.Namespace(
            daemonize=True,
            daemon_host=target_host,
            daemon_port=target_port,
            http_host="",
            http_port=None,
        )
        return _cmd_daemon_start_impl(start_args)

    return run_with_lifecycle_lock("refresh", _action)
