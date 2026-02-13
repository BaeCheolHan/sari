import argparse
import os
from typing import Callable, Optional


DaemonParams = dict[str, object]


def needs_upgrade_or_drain(
    identify: Optional[dict],
    *,
    local_version: str,
) -> bool:
    if not identify:
        return False
    existing_version = identify.get("version") or ""
    draining = bool(identify.get("draining"))
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

    explicit_port = bool(_arg("daemon_port"))
    force_start = (os.environ.get("SARI_DAEMON_FORCE_START") or "").strip().lower() in {"1", "true", "yes", "on"}

    if _arg("daemon_host") or _arg("daemon_port"):
        host = _arg("daemon_host") or default_host
        port = int(_arg("daemon_port") or default_port)
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

    if bool(_arg("all")):
        host, port = None, None
        all_mode = True
    elif _arg("daemon_host") or _arg("daemon_port"):
        host = _arg("daemon_host") or default_host
        port = int(_arg("daemon_port") or default_port)
        all_mode = False
    else:
        host, port = None, None
        all_mode = True

    return {"host": host, "port": port, "all": all_mode}
