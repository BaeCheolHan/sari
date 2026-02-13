import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from sari.core.config import Config
from sari.core.constants import (
    DEFAULT_DAEMON_HOST,
    DEFAULT_DAEMON_PORT,
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
)
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT
from sari.core.server_registry import ServerRegistry
from sari.core.workspace import WorkspaceManager

_LAST_RESOLVER_STATUS = {"resolver_ok": True, "error": ""}


def _set_resolver_status(resolver_ok: bool, error: str = "") -> None:
    _LAST_RESOLVER_STATUS["resolver_ok"] = bool(resolver_ok)
    _LAST_RESOLVER_STATUS["error"] = str(error or "")


def get_last_resolver_status() -> dict:
    return dict(_LAST_RESOLVER_STATUS)


def _strict_ssot_enabled() -> bool:
    return (os.environ.get("SARI_STRICT_SSOT") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_legacy_server_info(workspace_root: str) -> Optional[dict]:
    if _strict_ssot_enabled():
        return None
    server_json = Path(workspace_root) / ".codex" / "tools" / "sari" / "data" / "server.json"
    if not server_json.exists():
        return None
    try:
        info = json.loads(server_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        endpoint = ServerRegistry().resolve_workspace_http(str(workspace_root))
        if endpoint:
            reg_host = str(endpoint.get("host") or "").strip()
            reg_port = endpoint.get("port")
            legacy_host = str(info.get("host") or "").strip()
            legacy_port = info.get("port")
            try:
                reg_port_i = int(reg_port) if reg_port is not None else None
                legacy_port_i = int(legacy_port) if legacy_port is not None else None
            except (TypeError, ValueError):
                reg_port_i = None
                legacy_port_i = None

            host_conflict = bool(legacy_host and reg_host and legacy_host != reg_host)
            port_conflict = (
                reg_port_i is not None
                and legacy_port_i is not None
                and reg_port_i != legacy_port_i
            )
            if host_conflict or port_conflict:
                return None
    except Exception:
        pass
    return info


def _load_http_config(workspace_root: str) -> Tuple[str, int]:
    try:
        cfg_path = WorkspaceManager.resolve_config_path(workspace_root)
        cfg = Config.load(cfg_path, workspace_root_override=workspace_root)
        host = str(getattr(cfg, "http_api_host", "") or DEFAULT_HTTP_HOST)
        port = int(getattr(cfg, "http_api_port", 0) or DEFAULT_HTTP_PORT)
        return host, port
    except Exception:
        return DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT


def resolve_http_endpoint(
    workspace_root: Optional[str] = None,
    host_override: Optional[str] = None,
    port_override: Optional[int] = None,
) -> Tuple[str, int]:
    env_host = os.environ.get("SARI_HTTP_API_HOST") or os.environ.get("SARI_HTTP_HOST")
    env_port = os.environ.get("SARI_HTTP_API_PORT") or os.environ.get("SARI_HTTP_PORT")
    root = workspace_root or os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()

    host, port = _load_http_config(str(root))

    try:
        resolved = ServerRegistry().resolve_workspace_http(str(root))
        if resolved:
            if resolved.get("host"):
                host = str(resolved.get("host"))
            if resolved.get("port"):
                port = int(resolved.get("port"))
        else:
            ws_info = ServerRegistry().get_workspace(str(root))
            if ws_info:
                if ws_info.get("http_host"):
                    host = str(ws_info.get("http_host"))
                if ws_info.get("http_port"):
                    port = int(ws_info.get("http_port"))
    except Exception:
        pass

    server_info = _load_legacy_server_info(str(root))
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


def resolve_registry_daemon_address(
    workspace_root: Optional[str] = None,
) -> Optional[Tuple[str, int]]:
    env_host = os.environ.get(RUNTIME_HOST)
    root = workspace_root or os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
    reg = ServerRegistry()

    inst = reg.resolve_latest_daemon(workspace_root=str(root), allow_draining=False)
    if not inst:
        inst = reg.resolve_workspace_daemon(str(root))

    if inst and inst.get("port"):
        host = inst.get("host") or (env_host or DEFAULT_DAEMON_HOST)
        return host, int(inst.get("port"))
    return None


def resolve_daemon_endpoint(workspace_root: Optional[str] = None) -> Tuple[str, int]:
    env_host = os.environ.get(RUNTIME_HOST)
    env_port = os.environ.get(RUNTIME_PORT)

    force_override = (os.environ.get("SARI_DAEMON_OVERRIDE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if force_override and env_port:
        try:
            _set_resolver_status(True, "")
            return (env_host or DEFAULT_DAEMON_HOST), int(env_port)
        except ValueError:
            pass

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

    if env_port:
        try:
            if not get_last_resolver_status().get("resolver_ok", True):
                _set_resolver_status(False, get_last_resolver_status().get("error", ""))
            else:
                _set_resolver_status(True, "")
            return (env_host or DEFAULT_DAEMON_HOST), int(env_port)
        except ValueError:
            pass

    if not get_last_resolver_status().get("resolver_ok", True):
        return (env_host or DEFAULT_DAEMON_HOST), DEFAULT_DAEMON_PORT
    _set_resolver_status(True, "")
    return (env_host or DEFAULT_DAEMON_HOST), DEFAULT_DAEMON_PORT
