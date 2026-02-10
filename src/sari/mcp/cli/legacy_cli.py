#!/usr/bin/env python3
"""
Sari CLI - Modern Command-line interface for daemon management.
"""
import sys
import os
import json
import argparse
import socket
import time
import urllib.parse
import urllib.request
import ipaddress
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry, get_registry_path
from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.constants import (
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
)

from .mcp_client import (
    identify_sari_daemon,
    probe_sari_daemon,
    ensure_workspace_http,
    request_mcp_status,
    is_http_running
)
from .smart_daemon import ensure_smart_daemon

# Tool executors
from sari.mcp.tools.grep_and_read import execute_grep_and_read
from sari.mcp.tools.search import execute_search
from sari.mcp.tools.save_snippet import execute_save_snippet
from sari.mcp.tools.get_snippet import execute_get_snippet
from sari.mcp.tools.dry_run_diff import execute_dry_run_diff

# Re-expose for test compatibility
is_daemon_running = probe_sari_daemon
_identify_sari_daemon = identify_sari_daemon
_is_http_running = is_http_running
_ensure_workspace_http = ensure_workspace_http
_request_mcp_status = request_mcp_status
_get_http_host_port_orig = None # Placeholder for later

def _arg(args: Any, name: str, default: Any = None) -> Any:
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
        if h == "localhost": return True
        return ipaddress.ip_address(h).is_loopback
    except ValueError: return False

def _enforce_loopback(host: str) -> None:
    if not _is_loopback(host):
        raise RuntimeError(f"Security error: {host} is not a loopback address.")

def _get_http_host_port(host_override: Optional[str] = None, port_override: Optional[int] = None) -> Tuple[str, int]:
    env_host = os.environ.get("SARI_HTTP_API_HOST") or os.environ.get("SARI_HTTP_HOST")
    env_port = os.environ.get("SARI_HTTP_API_PORT") or os.environ.get("SARI_HTTP_PORT")
    workspace_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    cfg = _load_config(str(workspace_root))
    host = host_override or env_host or cfg.http_api_host or DEFAULT_HTTP_HOST
    port = int(port_override or env_port or cfg.http_api_port or DEFAULT_HTTP_PORT)
    return host, port

def _resolve_http_endpoint_for_daemon(args: Any, daemon_host: str, daemon_port: int) -> Tuple[str, int]:
    host_override = _arg(args, "http_host")
    port_override = _arg(args, "http_port")
    if host_override or port_override is not None:
        return _get_http_host_port(host_override, port_override)

    host, port = _get_http_host_port(None, None)
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(daemon_host, daemon_port)
        if not inst:
            ws_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
            inst = reg.resolve_workspace_daemon(str(ws_root))
        if inst:
            if inst.get("http_host"):
                host = str(inst.get("http_host"))
            if inst.get("http_port"):
                port = int(inst.get("http_port"))
    except Exception:
        pass
    return host, port

def _request_http(path: str, params: dict, host: Optional[str] = None, port: Optional[int] = None) -> dict:
    h, p = _get_http_host_port(host, port)
    _enforce_loopback(h)
    url = f"http://{h}:{p}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=3.0) as r:
        return json.loads(r.read().decode("utf-8"))

def _ensure_daemon_running(h, p, **kwargs):
    """Bridge for ensure_smart_daemon (test compatibility)."""
    res_h, res_p = ensure_smart_daemon(h, p)
    return res_h, res_p, True

def cmd_daemon_start(args):
    from .daemon import extract_daemon_start_params, handle_existing_daemon, check_port_availability, prepare_daemon_environment, start_daemon_in_background, start_daemon_in_foreground
    params = extract_daemon_start_params(args)
    if (res := handle_existing_daemon(params)) is not None: return res
    if (res := check_port_availability(params)) is not None: return res
    prepare_daemon_environment(params)
    return start_daemon_in_background(params) if _arg(args, "daemonize") else start_daemon_in_foreground(params)

def cmd_daemon_stop(args):
    from .daemon import extract_daemon_stop_params, stop_daemon_process
    return stop_daemon_process(extract_daemon_stop_params(args))

def cmd_daemon_status(args):
    explicit = bool(_arg(args, "daemon_host") or _arg(args, "daemon_port"))
    if explicit:
        host = _arg(args, "daemon_host") or DEFAULT_DAEMON_HOST
        port = int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)
        running = is_daemon_running(host, port)
        identity = identify_sari_daemon(host, port) if running else None
        print(f"Host: {host}\nPort: {port}\nStatus: {'🟢 Running' if running else '⚫ Stopped'}")
        if identity and (root := identity.get("workspaceRoot")): print(f"Workspace Root: {root}")
        if identity and (pid := identity.get("pid")): print(f"PID: {pid}")
        return 0 if running else 1

    from .daemon import list_registry_daemons
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
    host, port = (_arg(args, "daemon_host") or DEFAULT_DAEMON_HOST, int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)) if _arg(args, "daemon_host") or _arg(args, "daemon_port") else get_daemon_address()
    h, p, _ = _ensure_daemon_running(host, port)
    if probe_sari_daemon(h, p):
        if ensure_workspace_http(h, p): return 0
    print("❌ Failed to ensure daemon services.")
    return 1

def cmd_daemon_refresh(args):
    stop_args = argparse.Namespace(daemon_host=None, daemon_port=None)
    stop_rc = cmd_daemon_stop(stop_args)
    if stop_rc != 0:
        return stop_rc
    start_args = argparse.Namespace(
        daemonize=True,
        daemon_host=_arg(args, "daemon_host", "") or "",
        daemon_port=_arg(args, "daemon_port"),
        http_host="",
        http_port=None,
    )
    return cmd_daemon_start(start_args)

def cmd_proxy(args):
    from sari.mcp.proxy import main as proxy_main
    proxy_main()

def cmd_auto(args):
    host, port = get_daemon_address()
    if not probe_sari_daemon(host, port):
        ensure_smart_daemon(host, port)
        for _ in range(20):
            if probe_sari_daemon(host, port): break
            time.sleep(0.2)
    if probe_sari_daemon(host, port): return cmd_proxy(args)
    print("❌ Daemon failed to start.", file=sys.stderr); return 1

def cmd_status(args):
    try:
        # Resolve daemon address first
        d_host, d_port = (_arg(args, "daemon_host") or DEFAULT_DAEMON_HOST, int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)) if _arg(args, "daemon_host") or _arg(args, "daemon_port") else get_daemon_address()
        daemon_running = is_daemon_running(d_host, d_port)
        
        # Resolve HTTP endpoint for selected daemon (registry-aware).
        h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
        http_running = _is_http_running(h, p)

        if not http_running:
            if not daemon_running:
                d_host, d_port, daemon_running = _ensure_daemon_running(d_host, d_port, allow_upgrade=False)
                h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
            if daemon_running:
                for _ in range(5):
                    _ensure_workspace_http(d_host, d_port)
                    h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
                    http_running = _is_http_running(h, p)
                    if http_running: break
                    time.sleep(0.1)
            
            if not http_running and daemon_running:
                fallback = _request_mcp_status(d_host, d_port)
                if fallback: print(json.dumps(fallback, ensure_ascii=False, indent=2)); return 0
            
            if not http_running:
                print(f"❌ Error: Sari services not running. Daemon: {d_host}:{d_port}, HTTP: {h}:{p}"); return 1

        data = _request_http("/status", {}, h, p)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as e: print(f"❌ Error: {e}"); return 1

def cmd_search(args):
    """Query HTTP search endpoint."""
    params = {"q": args.query, "limit": args.limit}
    if _arg(args, "repo"): params["repo"] = args.repo
    data = _request_http("/search", params)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0

def cmd_doctor(args):
    from sari.mcp.tools.doctor import execute_doctor
    payload = execute_doctor({
        "auto_fix": bool(_arg(args, "auto_fix")), "auto_fix_rescan": bool(_arg(args, "auto_fix_rescan")),
        "include_network": not _arg(args, "no_network"), "include_db": not _arg(args, "no_db"),
        "include_port": not _arg(args, "no_port"), "include_disk": not _arg(args, "no_disk"),
        "min_disk_gb": float(_arg(args, "min_disk_gb", 1.0)),
    })
    print(payload.get("content", [{}])[0].get("text", ""))
    return 0

def cmd_init(args):
    ws_root = Path(_arg(args, "workspace") or WorkspaceManager.resolve_workspace_root()).expanduser().resolve()
    cfg_path = Path(WorkspaceManager.resolve_config_path(str(ws_root)))
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(cfg_path.read_text()) if cfg_path.exists() and not _arg(args, "force") else {}
    roots = list(dict.fromkeys((data.get("roots") or []) + [str(ws_root)]))
    data.update({"roots": roots, "db_path": data.get("db_path", Config.get_defaults(str(ws_root))["db_path"])})
    cfg_path.write_text(json.dumps(data, indent=2)); print(f"✅ Workspace initialized at {ws_root}"); return 0

def cmd_prune(args):
    db, _, _ = _load_local_db(_arg(args, "workspace"))
    try:
        tables = [_arg(args, "table")] if _arg(args, "table") else ["snippets", "failed_tasks", "contexts"]
        for t in tables:
            count = db.prune_data(t, _arg(args, "days") or 30)
            if count > 0: print(f"🧹 {t}: Removed {count} records.")
        return 0
    finally: db.close()

def read_pid(host: Optional[str] = None, port: Optional[int] = None) -> Optional[int]:
    """Read daemon pid from registry (backward compat for tests)."""
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(host, port) if host and port else None
        if not inst:
            ws_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
            inst = reg.resolve_workspace_daemon(str(ws_root))
        return int(inst["pid"]) if inst and inst.get("pid") else None
    except Exception: return None

def main():
    parser = argparse.ArgumentParser(prog="sari", description="Sari CLI", formatter_class=argparse.RawTextHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    d_parser = subparsers.add_parser("daemon", help="Daemon").add_subparsers(dest="daemon_command")
    start = d_parser.add_parser("start", help="Start"); start.add_argument("-d", "--daemonize", action="store_true"); start.add_argument("--daemon-host", default=""); start.add_argument("--daemon-port", type=int); start.set_defaults(func=cmd_daemon_start)
    stop = d_parser.add_parser("stop", help="Stop")
    stop.add_argument("--daemon-host", default="")
    stop.add_argument("--daemon-port", type=int)
    stop.set_defaults(func=cmd_daemon_stop); status = d_parser.add_parser("status", help="Status"); status.add_argument("--daemon-host", default=""); status.add_argument("--daemon-port", type=int); status.set_defaults(func=cmd_daemon_status); ensure = d_parser.add_parser("ensure", help="Ensure"); ensure.add_argument("--daemon-host", default=""); ensure.add_argument("--daemon-port", type=int); ensure.set_defaults(func=cmd_daemon_ensure)
    refresh = d_parser.add_parser("refresh", help="Refresh"); refresh.add_argument("--daemon-host", default=""); refresh.add_argument("--daemon-port", type=int); refresh.set_defaults(func=cmd_daemon_refresh)
    subparsers.add_parser("proxy", help="Proxy").set_defaults(func=cmd_proxy); subparsers.add_parser("auto", help="Auto").set_defaults(func=cmd_auto); st = subparsers.add_parser("status", help="HTTP Status"); st.add_argument("--daemon-host", default=""); st.add_argument("--daemon-port", type=int); st.add_argument("--http-host", default=""); st.add_argument("--http-port", type=int); st.set_defaults(func=cmd_status)
    doc = subparsers.add_parser("doctor", help="Doctor"); doc.add_argument("--auto-fix", action="store_true"); doc.add_argument("--auto-fix_rescan", action="store_true"); doc.add_argument("--no-network", action="store_true"); doc.add_argument("--no-db", action="store_true"); doc.add_argument("--no-port", action="store_true"); doc.add_argument("--no-disk", action="store_true"); doc.add_argument("--min-disk-gb", type=float, default=1.0); doc.set_defaults(func=cmd_doctor)
    init = subparsers.add_parser("init", help="Init"); init.add_argument("--workspace", default=""); init.add_argument("--force", action="store_true"); init.set_defaults(func=cmd_init)
    prune = subparsers.add_parser("prune", help="Prune"); prune.add_argument("--days", type=int); prune.add_argument("--table", choices=["snippets", "failed_tasks", "contexts"]); prune.add_argument("--workspace", default=""); prune.set_defaults(func=cmd_prune)
    args = parser.parse_args(); return args.func(args) if hasattr(args, "func") else 0

if __name__ == "__main__":
    sys.exit(main())
