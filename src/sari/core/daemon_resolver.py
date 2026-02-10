import os
from typing import Tuple, Optional
from sari.core.server_registry import ServerRegistry
from sari.core.workspace import WorkspaceManager
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT

DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT

def resolve_daemon_address(workspace_root: Optional[str] = None) -> Tuple[str, int]:
    """
    Single Source of Truth for resolving daemon address.
    Priority:
      1. Env Override (Explicit debugging) -> Highest priority
      2. Registry SSOT (resolve_latest_daemon) -> Ensures version/draining awareness
      3. Env Fallback (Legacy)
      4. Default
    """
    env_host = os.environ.get("SARI_DAEMON_HOST")
    env_port = os.environ.get("SARI_DAEMON_PORT")
    
    # 1. Env Override (Explicit only - High priority for debugging)
    force_override = (os.environ.get("SARI_DAEMON_OVERRIDE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if force_override and env_port:
        try:
            return (env_host or DEFAULT_HOST), int(env_port)
        except ValueError:
            pass

    # 2. Check Registry (SSOT)
    try:
        root = workspace_root or os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        reg = ServerRegistry()
        
        # Phase 1: Try to find the absolute best daemon (non-draining, latest)
        inst = reg.resolve_latest_daemon(workspace_root=str(root), allow_draining=False)
        
        # Fallback to current bound daemon if no 'latest' found (might be first boot)
        if not inst:
            inst = reg.resolve_workspace_daemon(str(root))
            
        if inst and inst.get("port"):
            host = inst.get("host") or (env_host or DEFAULT_HOST)
            return host, int(inst.get("port"))
    except Exception:
        pass

    # 3. Env Fallback (if no registry entry found)
    if env_port:
        try:
            return (env_host or DEFAULT_HOST), int(env_port)
        except ValueError:
            pass

    return (env_host or DEFAULT_HOST), DEFAULT_PORT
