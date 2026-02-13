#!/usr/bin/env python3
"""
Sari CLI - Modern Command-line interface for daemon management.
"""
from .commands.daemon_commands import (
    cmd_daemon_start,
    cmd_daemon_stop,
    cmd_daemon_status,
    cmd_daemon_ensure,
    cmd_daemon_refresh,
)
from .commands.maintenance_commands import (
    cmd_doctor,
    cmd_init,
    cmd_prune,
    cmd_vacuum,
)
from .commands.status_commands import (
    cmd_status,
    cmd_search,
)
import sys
import os
import json
import argparse
import time
import urllib.parse
import urllib.request
import ipaddress
from typing import Optional

from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import ServerRegistry
from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.endpoint_resolver import resolve_http_endpoint

from .mcp_client import (
    identify_sari_daemon,
    probe_sari_daemon,
    ensure_workspace_http,
    request_mcp_status,
    is_http_running
)
from .smart_daemon import ensure_smart_daemon

# Tool executors

# Re-expose for test compatibility
is_daemon_running = probe_sari_daemon
_identify_sari_daemon = identify_sari_daemon
_is_http_running = is_http_running
_ensure_workspace_http = ensure_workspace_http
_request_mcp_status = request_mcp_status
_get_http_host_port_orig = None  # Placeholder for later


def _arg(args: object, name: str, default: object = None) -> object:
    """Safe argument access for Namespace objects."""
    return getattr(args, name, default) if hasattr(args, name) else default


def _load_config(workspace_root: str) -> Config:
    cfg_path = WorkspaceManager.resolve_config_path(workspace_root)
    return Config.load(cfg_path, workspace_root_override=workspace_root)


def _load_local_db(workspace_root: Optional[str] = None):
    root = workspace_root or WorkspaceManager.resolve_workspace_root()
    cfg = _load_config(root)
    db = LocalSearchDB(cfg.db_path)
    return db, cfg.workspace_roots, root


def _is_loopback(host: str) -> bool:
    try:
        h = (host or "").strip().lower()
        if h == "localhost":
            return True
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _enforce_loopback(host: str) -> None:
    if not _is_loopback(host):
        raise RuntimeError(
            f"Security error: {host} is not a loopback address.")


def _get_http_host_port(
        host_override: Optional[str] = None, port_override: Optional[int] = None) -> tuple[str, int]:
    workspace_root = os.environ.get(
        "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    return resolve_http_endpoint(
        workspace_root=str(workspace_root),
        host_override=host_override,
        port_override=port_override,
    )


def _resolve_http_endpoint_for_daemon(
        args: object, daemon_host: str, daemon_port: int) -> tuple[str, int]:
    host_override = _arg(args, "http_host")
    port_override = _arg(args, "http_port")
    if host_override or port_override is not None:
        return _get_http_host_port(host_override, port_override)

    host, port = _get_http_host_port(None, None)
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(daemon_host, daemon_port)
        if not inst:
            ws_root = os.environ.get(
                "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
            inst = reg.resolve_workspace_daemon(str(ws_root))
        if inst:
            if inst.get("http_host"):
                host = str(inst.get("http_host"))
            if inst.get("http_port"):
                port = int(inst.get("http_port"))
    except Exception:
        pass
    return host, port


def _request_http(
        path: str,
        params: dict,
        host: Optional[str] = None,
        port: Optional[int] = None) -> dict:
    h, p = _get_http_host_port(host, port)
    _enforce_loopback(h)
    url = f"http://{h}:{p}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=3.0) as r:
        return json.loads(r.read().decode("utf-8"))


def _ensure_daemon_running(h, p, **kwargs):
    """Bridge for ensure_smart_daemon (test compatibility)."""
    res_h, res_p = ensure_smart_daemon(h, p)
    return res_h, res_p, True


def cmd_proxy(args):
    from sari.mcp.proxy import main as proxy_main
    proxy_main()


def cmd_auto(args):
    host, port = get_daemon_address()
    if not probe_sari_daemon(host, port):
        ensure_smart_daemon(host, port)
        for _ in range(20):
            if probe_sari_daemon(host, port):
                break
            time.sleep(0.2)
    if probe_sari_daemon(host, port):
        return cmd_proxy(args)
    print("❌ Daemon failed to start.", file=sys.stderr)
    return 1


def read_pid(host: Optional[str] = None,
             port: Optional[int] = None) -> Optional[int]:
    """Read daemon pid from registry (backward compat for tests)."""
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(
            host, port) if host and port else None
        if not inst:
            ws_root = os.environ.get(
                "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
            inst = reg.resolve_workspace_daemon(str(ws_root))
        return int(inst["pid"]) if inst and inst.get("pid") else None
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sari",
        description="Sari CLI",
        formatter_class=argparse.RawTextHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    d_parser = subparsers.add_parser(
        "daemon", help="Daemon").add_subparsers(
        dest="daemon_command")
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
    prune.add_argument(
        "--table",
        choices=[
            "snippets",
            "failed_tasks",
            "contexts"])
    prune.add_argument("--workspace", default="")
    prune.set_defaults(func=cmd_prune)
    vacuum = subparsers.add_parser("vacuum", help="VACUUM sqlite database")
    vacuum.add_argument("--workspace", default="")
    vacuum.set_defaults(func=cmd_vacuum)
    args = parser.parse_args(argv)
    return args.func(args) if hasattr(args, "func") else 0


if __name__ == "__main__":
    sys.exit(main())
