import sys
import json
import socket
import threading
import os
import time
import subprocess
import logging
import tempfile
import secrets
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.daemon_resolver import resolve_daemon_address as _resolve_daemon_target
from sari.core.utils.ipc import flock, funlock, parse_mcp_headers, read_mcp_message as _utils_read_mcp

logger = logging.getLogger("mcp-proxy")

MAX_MESSAGE_SIZE = 10 * 1024 * 1024 
_MODE_FRAMED = "framed"
_MODE_JSONL = "jsonl"

# --- RESTORED FOR TESTS ---
def _read_mcp_message(stdin):
    """Read one MCP message from stream (Content-Length framed or JSONL)."""
    line = stdin.readline()
    if not line: return None
    if line.strip().startswith(b"{"):
        return line.strip(), _MODE_JSONL
    
    # Framed mode
    headers = {line.split(b":", 1)[0].strip().lower().decode(): line.split(b":", 1)[1].strip().decode()}
    while True:
        h = stdin.readline()
        if not h or h == b"\r\n" or h == b"\n": break
        if b":" in h:
            k, v = h.split(b":", 1)
            headers[k.strip().lower().decode()] = v.strip().decode()
            
    content_length = int(headers.get("content-length", 0))
    if content_length <= 0: return None
    return stdin.read(content_length), _MODE_FRAMED

def _identify_sari_daemon(host: str, port: int, timeout: float = 0.3) -> Optional[dict]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "sari/identify"}).encode()
            sock.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            f = sock.makefile("rb")
            headers = parse_mcp_headers(f)
            resp = _utils_read_mcp(f, headers)
            if resp: return json.loads(resp.decode()).get("result")
    except: pass
    return None

def start_daemon_if_needed(host, port, workspace_root: str = ""):
    if _identify_sari_daemon(host, port): return True
    lock_path = os.path.join(tempfile.gettempdir(), f"sari-daemon-{host}-{port}.lock")
    with open(lock_path, "w") as f:
        flock(f)
        try:
            if _identify_sari_daemon(host, port): return True
            subprocess.Popen([sys.executable, "-m", "sari.mcp.daemon"], start_new_session=True)
            for _ in range(50):
                h, p = _resolve_daemon_target()
                if _identify_sari_daemon(h, p): return True
                time.sleep(0.1)
            return False
        finally: funlock(f)

def _reconnect(state) -> bool:
    # Priority 1: Smart reconnection using registry
    reg = ServerRegistry()
    for _ in range(5):
        latest = reg.resolve_latest_daemon(workspace_root=state.get("workspace_root"))
        host, port = (latest["host"], latest["port"]) if latest else _resolve_daemon_target()
        if start_daemon_if_needed(host, port):
            try:
                state["sock"] = socket.create_connection((host, port))
                state["dead"] = False
                return True
            except: time.sleep(0.2)
    return False

def _send_payload(state, payload: bytes, mode: str) -> None:
    sock = state.get("sock")
    if not sock: return
    if mode == _MODE_JSONL: sock.sendall(payload + b"\n")
    else: sock.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)

def forward_socket_to_stdout(sock, state):
    try:
        f = sock.makefile("rb")
        while True:
            headers = parse_mcp_headers(f)
            if not headers: break
            body = _utils_read_mcp(f, headers)
            if not body: break
            # Draining logic
            obj = json.loads(body.decode())
            if isinstance(obj, dict) and obj.get("error", {}).get("code") == -32001:
                state["dead"] = True
                if _reconnect(state): continue
            sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            sys.stdout.buffer.flush()
    except: pass
    finally: state["dead"] = True

def main():
    host, port = _resolve_daemon_target()
    if not start_daemon_if_needed(host, port): sys.exit(1)
    sock = socket.create_connection((host, port))
    state = {"sock": sock, "dead": False, "conn_lock": threading.Lock(), "workspace_root": os.environ.get("SARI_WORKSPACE_ROOT")}
    threading.Thread(target=forward_socket_to_stdout, args=(sock, state), daemon=True).start()
    while True: time.sleep(1)

if __name__ == "__main__": main()
