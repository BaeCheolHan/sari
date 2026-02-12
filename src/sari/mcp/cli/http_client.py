"""
HTTP client for Sari HTTP server.

This module handles HTTP communication with the Sari HTTP API server.
"""

import os
import json
import urllib.parse
import urllib.request
from typing import Optional

from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import ServerRegistry
from sari.core.constants import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    HTTP_CHECK_TIMEOUT_SECONDS,
)

from .utils import load_config, enforce_loopback
from .registry import load_server_info


def get_http_host_port(
    host_override: Optional[str] = None,
    port_override: Optional[int] = None
) -> tuple[str, int]:
    """
    Get active HTTP server address with priority resolution.
    
    Priority order (lowest to highest):
    1. Config file defaults
    2. Registry workspace info
    3. Legacy server.json
    4. Environment variables
    5. Explicit overrides
    
    Args:
        host_override: Optional explicit host override
        port_override: Optional explicit port override
    
    Returns:
        Tuple of (host, port)
    """
    env_host = os.environ.get("SARI_HTTP_API_HOST") or os.environ.get("SARI_HTTP_HOST")
    env_port = os.environ.get("SARI_HTTP_API_PORT") or os.environ.get("SARI_HTTP_PORT")
    
    # Respect SARI_WORKSPACE_ROOT environment variable for testing
    workspace_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    cfg = load_config(str(workspace_root))

    # Priority: config (lowest) → registry → server.json → env → override (highest)
    host = cfg.http_api_host or DEFAULT_HTTP_HOST
    port = int(cfg.http_api_port or DEFAULT_HTTP_PORT)

    try:
        resolved = ServerRegistry().resolve_workspace_http(str(workspace_root))
        if resolved:
            if resolved.get("host"):
                host = str(resolved.get("host"))
            if resolved.get("port"):
                port = int(resolved.get("port"))
        else:
            # Backward-compat: workspace-level endpoint
            ws_info = ServerRegistry().get_workspace(str(workspace_root))
            if ws_info:
                if ws_info.get("http_host"):
                    host = str(ws_info.get("http_host"))
                if ws_info.get("http_port"):
                    port = int(ws_info.get("http_port"))
    except Exception:
        pass

    server_info = load_server_info(str(workspace_root))
    if server_info:
        try:
            if server_info.get("host"):
                host = str(server_info.get("host"))
            if server_info.get("port"):
                port = int(server_info.get("port"))
        except Exception:
            pass

    if env_host:
        host = env_host
    if env_port:
        try:
            port = int(env_port)
        except (TypeError, ValueError):
            pass

    if host_override:
        host = host_override
    if port_override is not None:
        port = int(port_override)

    return host, port


def request_http(
    path: str,
    params: dict,
    host: Optional[str] = None,
    port: Optional[int] = None
) -> dict:
    """
    Make HTTP request to Sari HTTP server.
    
    Args:
        path: URL path (e.g., "/search")
        params: Query parameters
        host: Optional host override
        port: Optional port override
    
    Returns:
        JSON response as dict
    
    Raises:
        RuntimeError: If host is not loopback
        urllib.error.URLError: If request fails
    """
    host, port = get_http_host_port(host, port)
    enforce_loopback(host)
    qs = urllib.parse.urlencode(params)
    url = f"http://{host}:{port}{path}?{qs}"
    with urllib.request.urlopen(url, timeout=3.0) as r:
        return json.loads(r.read().decode("utf-8"))


def is_http_running(
    host: str,
    port: int,
    timeout: float = HTTP_CHECK_TIMEOUT_SECONDS
) -> bool:
    """
    Check if HTTP server is running.
    
    Args:
        host: Server host
        port: Server port
        timeout: Request timeout in seconds
    
    Returns:
        True if server is healthy, False otherwise
    """
    enforce_loopback(host)
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return False
            payload = json.loads(r.read().decode("utf-8"))
            return bool(payload.get("ok"))
    except Exception:
        return False
