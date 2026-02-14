"""
MCP client for Sari daemon.

This module handles MCP (Model Context Protocol) communication with the Sari daemon.
"""

import os
import json
import socket
from typing import Optional, TypeAlias

from sari.core.workspace import WorkspaceManager
from sari.core.constants import (
    DAEMON_IDENTIFY_TIMEOUT_SECONDS,
    DAEMON_PROBE_TIMEOUT_SECONDS,
)
from sari.mcp.cli.utils import enforce_loopback

JsonMap: TypeAlias = dict[str, object]
_MAX_MCP_HEADER_LINES = 64
_MAX_MCP_CONTENT_LENGTH = 4 * 1024 * 1024


def _send_framed_json(sock: socket.socket, payload: JsonMap) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sock.sendall(header + body)


def _read_framed_json(sock: socket.socket) -> JsonMap:
    f = sock.makefile("rb")
    headers: dict[bytes, bytes] = {}
    for _ in range(_MAX_MCP_HEADER_LINES):
        line = f.readline()
        if not line:
            return {}
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
    content_len_raw = headers.get(b"content-length", b"0")
    try:
        content_length = int(content_len_raw)
    except (TypeError, ValueError):
        return {}
    if content_length <= 0:
        return {}
    if content_length > _MAX_MCP_CONTENT_LENGTH:
        raise ValueError("mcp content-length too large")
    resp_body = f.read(content_length)
    if not resp_body:
        return {}
    parsed = json.loads(resp_body.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def identify_sari_daemon(
    host: str,
    port: int,
    timeout: float = DAEMON_IDENTIFY_TIMEOUT_SECONDS
) -> Optional[JsonMap]:
    """
    Identify if server is a Sari daemon using MCP protocol.

    Tries two methods:
    1. Modern: sari/identify method
    2. Legacy: ping method with "Server not initialized" error

    Args:
        host: Daemon host
        port: Daemon port
        timeout: Connection timeout in seconds

    Returns:
        Identify payload dict if Sari daemon, None otherwise
    """
    try:
        enforce_loopback(host)
    except RuntimeError:
        return None

    # Try modern sari/identify method
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            _send_framed_json(sock, {"jsonrpc": "2.0", "id": 1, "method": "sari/identify"})
            resp = _read_framed_json(sock)

            result_raw = resp.get("result")
            result = result_raw if isinstance(result_raw, dict) else {}
            if result.get("name") == "sari":
                return result
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError):
        pass

    # Legacy fallback: probe "ping" and accept "Server not initialized" error
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            _send_framed_json(sock, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            resp = _read_framed_json(sock)
            err_raw = resp.get("error")
            err = err_raw if isinstance(err_raw, dict) else {}
            msg = (err.get("message") or "").lower()
            if "server not initialized" in msg:
                return {
                    "name": "sari",
                    "version": "legacy",
                    "protocolVersion": ""}
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError):
        pass

    return None


def probe_sari_daemon(
    host: str,
    port: int,
    timeout: float = DAEMON_PROBE_TIMEOUT_SECONDS
) -> bool:
    """
    Verify the server speaks Sari MCP (framed JSON-RPC).

    Args:
        host: Daemon host
        port: Daemon port
        timeout: Connection timeout in seconds

    Returns:
        True if Sari daemon, False otherwise
    """
    return identify_sari_daemon(host, port, timeout=timeout) is not None


def is_http_running(host: str, port: int, timeout: float = 2.0) -> bool:
    """
    Check if Sari HTTP API server is responding.
    """
    import urllib.request
    import json
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return False
            payload = json.loads(r.read().decode("utf-8"))
            return bool(payload.get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def ensure_workspace_http(
    daemon_host: str,
    daemon_port: int,
    workspace_root: Optional[str] = None,
    timeout: float = 5.0,
) -> bool:
    """
    Ensure workspace is initialized so HTTP server is started/registered.

    Args:
        daemon_host: Daemon host
        daemon_port: Daemon port
        workspace_root: Optional workspace root (auto-detected if None)

    Returns:
        True if successful, False otherwise
    """
    try:
        enforce_loopback(daemon_host)
        root = workspace_root or os.environ.get(
            "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        with socket.create_connection((daemon_host, daemon_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            _send_framed_json(sock, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "rootUri": f"file://{root}",
                    "capabilities": {},
                    # Keep initialized workspaces available through daemon lifetime.
                    "sariPersist": True,
                },
            })
            resp = _read_framed_json(sock)
            if isinstance(resp.get("error"), dict):
                return False
        return True
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError, RuntimeError):
        return False


def request_mcp_status(
    daemon_host: str,
    daemon_port: int,
    workspace_root: Optional[str] = None
) -> Optional[JsonMap]:
    """
    Request status from daemon via MCP.

    Args:
        daemon_host: Daemon host
        daemon_port: Daemon port
        workspace_root: Optional workspace root (auto-detected if None)

    Returns:
        Status dict or None if request fails
    """
    try:
        enforce_loopback(daemon_host)
        root = workspace_root or os.environ.get(
            "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        with socket.create_connection((daemon_host, daemon_port), timeout=2.0) as sock:
            sock.settimeout(2.0)
            _send_framed_json(sock, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"rootUri": f"file://{root}", "capabilities": {}},
            })
            _ = _read_framed_json(sock)
            _send_framed_json(sock, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {"details": True}},
            })
            resp = _read_framed_json(sock)
            if isinstance(resp.get("error"), dict):
                return None
            return resp.get("result") or resp
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError, RuntimeError):
        return None
