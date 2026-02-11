"""
Registry operations for Sari CLI.

This module handles server registry and server info operations.
"""

import json
from pathlib import Path
from typing import Optional, TypeAlias

from sari.core.server_registry import get_registry_path

JsonMap: TypeAlias = dict[str, object]
InstancesMap: TypeAlias = dict[str, JsonMap]

def load_registry_instances() -> InstancesMap:
    """
    Load all daemon instances from registry.
    
    Returns:
        Dictionary of registry instances, or empty dict if unavailable
    """
    try:
        reg_file = get_registry_path()
        if reg_file.exists():
            return json.loads(reg_file.read_text(encoding="utf-8")).get("instances", {})
    except Exception:
        pass
    return {}


def load_server_info(workspace_root: str) -> Optional[dict]:
    """
    Load server info from legacy server.json location.
    
    This is for backward compatibility with older Sari versions.
    
    Args:
        workspace_root: Workspace root directory
    
    Returns:
        Server info dict or None if not found
    """
    server_json = Path(workspace_root) / ".codex" / "tools" / "sari" / "data" / "server.json"
    if not server_json.exists():
        return None
    try:
        return json.loads(server_json.read_text(encoding="utf-8"))
    except Exception:
        return None
