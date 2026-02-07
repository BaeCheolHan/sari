import sys
import os
import argparse
import json
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import ipaddress
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.utils.ipc import parse_mcp_headers, read_mcp_message
from sari.core.config import Config
from sari.core.db import LocalSearchDB

# --- PUBLIC UTILITIES FOR TESTS ---
def is_daemon_running(host: str, port: int) -> bool:
    return _identify_sari_daemon(host, port) is not None

def read_pid(host: str, port: int) -> Optional[int]:
    reg = ServerRegistry(); inst = reg.resolve_daemon_by_endpoint(host, port)
    return inst.get("pid") if inst else None

def _identify_sari_daemon(host: str, port: int, timeout: float = 1.0) -> Optional[dict]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "sari/identify"}).encode()
            sock.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            f = sock.makefile("rb"); h = parse_mcp_headers(f); resp = read_mcp_message(f, h)
            if resp: return json.loads(resp.decode()).get("result")
    except: pass
    return None

def _get_http_host_port(host_override=None, port_override=None):
    ws_root = WorkspaceManager.resolve_workspace_root()
    reg = ServerRegistry(); ws_info = reg.get_workspace(ws_root)
    host = host_override or (ws_info.get("http_host") if ws_info else "127.0.0.1")
    port = port_override or (ws_info.get("http_port") if ws_info else 47777)
    return host, port

def _is_http_running(host: str, port: int) -> bool:
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=0.5) as r: return r.status == 200
    except: return False

def _load_local_db(workspace_root: Optional[str] = None):
    root = workspace_root or WorkspaceManager.resolve_workspace_root()
    cfg = Config.load(WorkspaceManager.resolve_config_path(root), workspace_root_override=root)
    return LocalSearchDB(cfg.db_path), cfg.workspace_roots, root

# --- COMMAND HANDLERS ---
def cmd_daemon_start(args):
    workspace_root = WorkspaceManager.resolve_workspace_root()
    host, port = get_daemon_address()
    if is_daemon_running(host, port): print(f"✅ Daemon already running on {host}:{port}"); return 0
    print(f"🚀 Starting Sari Daemon on {host}:{port}...")
    env = os.environ.copy(); env["SARI_DAEMON_PORT"] = str(port); env["SARI_WORKSPACE_ROOT"] = workspace_root
    if getattr(args, "daemonize", False):
        subprocess.Popen([sys.executable, "-m", "sari.mcp.daemon"], env=env, start_new_session=True); return 0
    else:
        from sari.mcp.daemon import SariDaemon
        SariDaemon(host=host, port=port).start(); return 0

def cmd_daemon_stop(args):
    host, port = get_daemon_address(); reg = ServerRegistry(); inst = reg.resolve_daemon_by_endpoint(host, port)
    if inst and inst.get("pid"):
        os.kill(inst["pid"], signal.SIGTERM); reg.unregister_daemon(inst["boot_id"])
        print("✅ Daemon stopped"); return 0
    print("Daemon is not running"); return 0

def cmd_daemon_status(args):
    host, port = get_daemon_address(); running = is_daemon_running(host, port)
    print("Daemon is running" if running else "Daemon is stopped"); return 0 if running else 1

def cmd_daemon_drain(args):
    host, port = get_daemon_address(); reg = ServerRegistry(); inst = reg.resolve_daemon_by_endpoint(host, port)
    if inst: reg._update(lambda d: d["daemons"][inst["boot_id"]].update({"draining": True}))
    print("⏳ Draining mode enabled."); return 0

def cmd_status(args):
    reg = ServerRegistry(); ws_info = reg.get_workspace(WorkspaceManager.resolve_workspace_root())
    if ws_info: print("🟢 Workspace Live"); return 0
    else: print("⚫ Workspace not active"); return 1

def cmd_init(args):
    ws_root = Path(args.workspace or WorkspaceManager.resolve_workspace_root()).resolve()
    cfg_path = Path(WorkspaceManager.resolve_config_path(str(ws_root)))
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"roots": [str(ws_root)], "db_path": str(ws_root/".sari"/"index.db")}, indent=2))
    print(f"✅ Initialized Sari at {ws_root}"); return 0

def cmd_doctor(args):
    from sari.mcp.tools.doctor import execute_doctor
    print(execute_doctor({"auto_fix": bool(getattr(args, "auto_fix", False))}).get("content", [{}])[0].get("text", "")); return 0

def cmd_search(args):
    host, port = _get_http_host_port(); url = f"http://{host}:{port}/search?q={urllib.parse.quote(args.query)}&limit={args.limit}"
    with urllib.request.urlopen(url) as r: print(json.dumps(json.loads(r.read()), indent=2, ensure_ascii=False)); return 0

def cmd_prune(args): print("🧹 Pruning data..."); return 0

def main():
    parser = argparse.ArgumentParser(prog="sari")
    subparsers = parser.add_subparsers(dest="command")
    daemon_p = subparsers.add_parser("daemon"); daemon_sub = daemon_p.add_subparsers()
    start_p = daemon_sub.add_parser("start"); start_p.add_argument("-d", "--daemonize", action="store_true"); start_p.set_defaults(func=cmd_daemon_start)
    daemon_sub.add_parser("stop").set_defaults(func=cmd_daemon_stop)
    daemon_sub.add_parser("status").set_defaults(func=cmd_daemon_status)
    daemon_sub.add_parser("drain").set_defaults(func=cmd_daemon_drain)
    subparsers.add_parser("status").set_defaults(func=cmd_status)
    subparsers.add_parser("proxy").set_defaults(func=lambda _: __import__("sari.mcp.proxy").mcp.proxy.main())
    subparsers.add_parser("init").add_argument("--workspace", default="").set_defaults(func=cmd_init)
    subparsers.add_parser("doctor").add_argument("--auto-fix", action="store_true").set_defaults(func=cmd_doctor)
    s_p = subparsers.add_parser("search"); s_p.add_argument("query"); s_p.add_argument("--limit", type=int, default=10); s_p.set_defaults(func=cmd_search)
    subparsers.add_parser("prune").set_defaults(func=cmd_prune)
    args = parser.parse_args()
    if hasattr(args, "func"): return args.func(args)
    parser.print_help()

if __name__ == "__main__": main()