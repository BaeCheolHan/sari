import argparse
import os
from typing import Callable, Optional


DaemonParams = dict[str, object]


def _coerce_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _parse_optional_port(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def needs_upgrade_or_drain(
    identify: Optional[dict],
    *,
    local_version: str,
) -> bool:
    if not identify:
        return False
    existing_version = identify.get("version") or ""
    draining = _coerce_bool(identify.get("draining"), False)
    needs_upgrade = bool(existing_version and local_version and existing_version != local_version)
    return bool(needs_upgrade or draining)


def extract_daemon_start_params(
    args: argparse.Namespace,
    *,
    workspace_root_resolver: Callable[[], str],
    registry_factory: Callable[[], object],
    daemon_address_resolver: Callable[[], tuple[str, int]],
    default_host: str,
    default_port: int,
) -> DaemonParams:
    def _arg(key: str):
        return getattr(args, key, None)

    workspace_root = os.environ.get("SARI_WORKSPACE_ROOT") or workspace_root_resolver()
    registry = registry_factory()

    daemon_port_arg = _parse_optional_port(_arg("daemon_port"))
    explicit_port = daemon_port_arg is not None
    force_start = (os.environ.get("SARI_DAEMON_FORCE_START") or "").strip().lower() in {"1", "true", "yes", "on"}

    if _arg("daemon_host") or daemon_port_arg is not None:
        host = _arg("daemon_host") or default_host
        port = daemon_port_arg if daemon_port_arg is not None else int(default_port)
    else:
        inst = registry.resolve_workspace_daemon(str(workspace_root))
        if inst and inst.get("port"):
            host = inst.get("host") or default_host
            port = int(inst.get("port"))
        else:
            host, port = daemon_address_resolver()

    return {
        "host": host,
        "port": port,
        "workspace_root": workspace_root,
        "registry": registry,
        "explicit_port": explicit_port,
        "force_start": force_start,
        "args": args,
    }


def extract_daemon_stop_params(
    args: argparse.Namespace,
    *,
    default_host: str,
    default_port: int,
) -> DaemonParams:
    def _arg(key: str):
        return getattr(args, key, None)

    daemon_port_arg = _parse_optional_port(_arg("daemon_port"))
    if _coerce_bool(_arg("all"), False):
        host, port = None, None
        all_mode = True
    elif _arg("daemon_host") or daemon_port_arg is not None:
        host = _arg("daemon_host") or default_host
        port = daemon_port_arg if daemon_port_arg is not None else int(default_port)
        all_mode = False
    else:
        host, port = None, None
        all_mode = True

    return {"host": host, "port": port, "all": all_mode}
