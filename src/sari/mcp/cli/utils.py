"""
Shared utilities for Sari CLI.

This module provides common utility functions used across CLI modules.
"""

import os
import socket
from pathlib import Path
from typing import Any, Optional
import ipaddress

try:
    import psutil
except ImportError:
    psutil = None

from sari.core.workspace import WorkspaceManager
from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.constants import (
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
)


# Legacy constants for backward compatibility
DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT
PID_FILE = WorkspaceManager.get_global_data_dir() / "daemon.pid"


def get_arg(args: Any, name: str, default: Any = None) -> Any:
    """
    Get argument value from args namespace.
    
    Args:
        args: Argument namespace from argparse
        name: Attribute name to retrieve
        default: Default value if attribute doesn't exist
    
    Returns:
        Attribute value or default
    """
    return getattr(args, name, default)


def get_pid_file_path() -> Path:
    """
    Get path to daemon PID file.
    
    Returns:
        Path to daemon.pid file
    """
    return WorkspaceManager.get_global_data_dir() / "daemon.pid"


def get_package_config_path() -> Path:
    """
    Get path to package configuration file.
    
    Returns:
        Path to config.json
    """
    return Path(__file__).parent.parent.parent / "config" / "config.json"


def load_config(workspace_root: str) -> Config:
    """
    Load configuration for workspace.
    
    Args:
        workspace_root: Workspace root directory
    
    Returns:
        Config object
    """
    cfg_path = WorkspaceManager.resolve_config_path(workspace_root)
    return Config.load(cfg_path, workspace_root_override=workspace_root)


def load_local_db(workspace_root: Optional[str] = None):
    """
    Load local search database and configuration.
    
    Args:
        workspace_root: Optional workspace root (auto-detected if None)
    
    Returns:
        Tuple of (db, workspace_roots, resolved_root)
    """
    root = workspace_root or WorkspaceManager.resolve_workspace_root()
    cfg_path = WorkspaceManager.resolve_config_path(root)
    cfg = Config.load(cfg_path, workspace_root_override=root)
    db = LocalSearchDB(cfg.db_path)
    return db, cfg.workspace_roots, root


def is_loopback(host: str) -> bool:
    """
    Check if host is a loopback address.
    
    Args:
        host: Hostname or IP address
    
    Returns:
        True if loopback, False otherwise
    """
    h = (host or "").strip().lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def enforce_loopback(host: str) -> None:
    """
    Enforce that host is a loopback address.
    
    Security: Always enforce loopback. No overrides allowed.
    
    Args:
        host: Hostname or IP address to check
    
    Raises:
        RuntimeError: If host is not a loopback address
    """
    if not is_loopback(host):
        raise RuntimeError(
            f"sari loopback-only: server_host must be 127.0.0.1/localhost/::1 (got={host}). "
            "Remote access is not allowed for security reasons."
        )


def get_local_version() -> str:
    """
    Get local Sari version.
    
    Returns:
        Version string or empty string if not available
    """
    try:
        from sari.version import __version__ as v
        return v or ""
    except Exception:
        return os.environ.get("SARI_VERSION", "") or ""


def is_port_in_use(host: str, port: int) -> bool:
    """
    Check if a port is already in use.
    
    Args:
        host: Host address
        port: Port number
    
    Returns:
        True if port is in use, False if available
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return False
    except OSError:
        return True


def is_tcp_blocked(err: OSError) -> bool:
    """
    Check if error indicates TCP is blocked.
    
    Args:
        err: OSError from socket operation
    
    Returns:
        True if error indicates TCP blocking
    """
    return getattr(err, "errno", None) in (1, 13)
