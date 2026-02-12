import os
import logging
from typing import Tuple, Optional
from sari.core.server_registry import ServerRegistry
from sari.core.workspace import WorkspaceManager
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT

DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT
_LAST_RESOLVER_STATUS = {"resolver_ok": True, "error": ""}


def _set_resolver_status(resolver_ok: bool, error: str = "") -> None:
    _LAST_RESOLVER_STATUS["resolver_ok"] = bool(resolver_ok)
    _LAST_RESOLVER_STATUS["error"] = str(error or "")


def get_last_resolver_status() -> dict:
    return dict(_LAST_RESOLVER_STATUS)


def resolve_registry_daemon_address(
    workspace_root: Optional[str] = None,
) -> Optional[Tuple[str, int]]:
    """
    Resolve daemon endpoint from registry only.

    Priority inside registry:
      1. Latest non-draining workspace daemon
      2. Workspace bound daemon (legacy/backward-compat)
    """
    env_host = os.environ.get(RUNTIME_HOST)
    root = workspace_root or os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    reg = ServerRegistry()

    inst = reg.resolve_latest_daemon(workspace_root=str(root), allow_draining=False)
    if not inst:
        inst = reg.resolve_workspace_daemon(str(root))

    if inst and inst.get("port"):
        host = inst.get("host") or (env_host or DEFAULT_HOST)
        return host, int(inst.get("port"))
    return None


def resolve_daemon_address(workspace_root: Optional[str] = None) -> Tuple[str, int]:
    """
    Single Source of Truth for resolving daemon address.
    Priority:
      1. Env Override (Explicit debugging) -> Highest priority
      2. Registry SSOT (resolve_latest_daemon) -> Ensures version/draining awareness
      3. Env Fallback (Legacy)
      4. Default
    """
    env_host = os.environ.get(RUNTIME_HOST)
    env_port = os.environ.get(RUNTIME_PORT)
    
    # 1. Env Override (Explicit only - High priority for debugging)
    force_override = (os.environ.get("SARI_DAEMON_OVERRIDE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if force_override and env_port:
        try:
            _set_resolver_status(True, "")
            return (env_host or DEFAULT_HOST), int(env_port)
        except ValueError:
            pass

    # 2. Check Registry (SSOT)
    try:
        resolved = resolve_registry_daemon_address(workspace_root=workspace_root)
        if resolved:
            _set_resolver_status(True, "")
            return resolved
    except Exception as e:
        logging.getLogger("sari.daemon_resolver").warning(
            "Failed to resolve daemon address from registry",
            exc_info=True,
        )
        _set_resolver_status(False, str(e))

    # 3. Env Fallback (if no registry entry found)
    if env_port:
        try:
            if not get_last_resolver_status().get("resolver_ok", True):
                _set_resolver_status(False, get_last_resolver_status().get("error", ""))
            else:
                _set_resolver_status(True, "")
            return (env_host or DEFAULT_HOST), int(env_port)
        except ValueError:
            pass

    if not get_last_resolver_status().get("resolver_ok", True):
        return (env_host or DEFAULT_HOST), DEFAULT_PORT
    _set_resolver_status(True, "")
    return (env_host or DEFAULT_HOST), DEFAULT_PORT
