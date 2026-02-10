"""
MCP client for Sari daemon.

This module handles MCP (Model Context Protocol) communication with the Sari daemon.
"""

import os
import json
import socket
from typing import Optional, Dict, Any

from sari.core.workspace import WorkspaceManager
from sari.core.constants import (
    DAEMON_IDENTIFY_TIMEOUT_SECONDS,
    DAEMON_PROBE_TIMEOUT_SECONDS,
)


def identify_sari_daemon(
    host: str,
    port: int,
    timeout: float = DAEMON_IDENTIFY_TIMEOUT_SECONDS
) -> Optional[Dict[str, Any]]:
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
    # Try modern sari/identify method
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            body = json.dumps({"jsonrpc": "2.0", "id": 1,
                              "method": "sari/identify"}).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            sock.sendall(header + body)

            f = sock.makefile("rb")
            headers = {}
            while True:
                line = f.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()

            try:
                content_length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                return None
            if content_length <= 0:
                return None
            resp_body = f.read(content_length)
            if not resp_body:
                return None
            resp = json.loads(resp_body.decode("utf-8"))

            result = resp.get("result") or {}
            if result.get("name") == "sari":
                return result
    except Exception:
        pass

    # Legacy fallback: probe "ping" and accept "Server not initialized" error
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            body = json.dumps({"jsonrpc": "2.0", "id": 1,
                              "method": "ping"}).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            sock.sendall(header + body)

            f = sock.makefile("rb")
            headers = {}
            while True:
                line = f.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()

            try:
                content_length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                return None
            if content_length <= 0:
                return None
            resp_body = f.read(content_length)
            if not resp_body:
                return None
            resp = json.loads(resp_body.decode("utf-8"))
            err = resp.get("error") or {}
            msg = (err.get("message") or "").lower()
            if "server not initialized" in msg:
                return {
                    "name": "sari",
                    "version": "legacy",
                    "protocolVersion": ""}
    except Exception:
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
    except Exception:
        return False


def ensure_workspace_http(
    daemon_host: str,
    daemon_port: int,
    workspace_root: Optional[str] = None
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
        root = workspace_root or os.environ.get(
            "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        with socket.create_connection((daemon_host, daemon_port), timeout=1.0) as sock:
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "rootUri": f"file://{root}",
                    "capabilities": {},
                    "sariPersist": True,
                },
            }).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            sock.sendall(header + body)
            f = sock.makefile("rb")
            headers = {}
            while True:
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()
            try:
                content_length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                content_length = 0
            if content_length > 0:
                f.read(content_length)
        return True
    except Exception:
        return False


def request_mcp_status(
    daemon_host: str,
    daemon_port: int,
    workspace_root: Optional[str] = None
) -> Optional[dict]:
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
        root = workspace_root or os.environ.get(
            "SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        with socket.create_connection((daemon_host, daemon_port), timeout=2.0) as sock:
            def _send(payload: dict) -> dict:
                body = json.dumps(payload).encode("utf-8")
                header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                sock.sendall(header + body)
                f = sock.makefile("rb")
                headers = {}
                while True:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        break
                    if b":" in line:
                        k, v = line.split(b":", 1)
                        headers[k.strip().lower()] = v.strip()
                try:
                    content_length = int(headers.get(b"content-length", b"0"))
                except ValueError:
                    content_length = 0
                body = f.read(content_length) if content_length > 0 else b""
                return json.loads(body.decode("utf-8")) if body else {}

            _send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"rootUri": f"file://{root}", "capabilities": {}},
            })
            resp = _send({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {"details": True}},
            })
            return resp.get("result") or resp
    except Exception:
        return None
