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
from sari.core.endpoint_resolver import resolve_http_endpoint
from sari.core.constants import (
    HTTP_CHECK_TIMEOUT_SECONDS,
)

from .utils import enforce_loopback


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
    workspace_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    return resolve_http_endpoint(
        workspace_root=str(workspace_root),
        host_override=host_override,
        port_override=port_override,
    )


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
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/"):
        raise RuntimeError("HTTP path must start with '/'")
    if not isinstance(params, dict):
        raise RuntimeError("params must be an object")
    qs = urllib.parse.urlencode(params)
    url = f"http://{host}:{port}{normalized_path}?{qs}"
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
