from __future__ import annotations

import os
import socket
from typing import Any


def _socket_probe(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _process_probe(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def build_daemon_state_projection(
    *,
    host: str,
    port: int,
    workspace_root: str | None = None,
    registry: Any | None = None,
    socket_probe: Any | None = None,
    process_probe: Any | None = None,
) -> dict[str, object]:
    reg = registry
    if reg is None:
        from sari.core.server_registry import ServerRegistry

        reg = ServerRegistry()
    socket_check = socket_probe or _socket_probe
    process_check = process_probe or _process_probe

    endpoint_entry = None
    workspace_entry = None
    try:
        endpoint_entry = reg.resolve_daemon_by_endpoint(str(host), int(port))
    except Exception:
        endpoint_entry = None
    if workspace_root:
        try:
            workspace_entry = reg.resolve_workspace_daemon(str(workspace_root))
        except Exception:
            workspace_entry = None

    selected = endpoint_entry or workspace_entry or {}
    pid = int(selected.get("pid") or 0) if isinstance(selected, dict) else 0
    reg_host = str(selected.get("host") or host) if isinstance(selected, dict) else str(host)
    reg_port = int(selected.get("port") or port) if isinstance(selected, dict) else int(port)
    reg_ok = bool(selected) and reg_port == int(port) and str(reg_host) == str(host)

    socket_ok = bool(socket_check(str(host), int(port)))
    process_ok = bool(process_check(pid)) if pid > 0 else False

    final_truth = "stopped"
    mismatch_reason = ""
    if socket_ok and (process_ok or pid <= 0):
        if reg_ok:
            final_truth = "running"
        else:
            final_truth = "degraded"
            mismatch_reason = "socket_live_without_registry"
    elif reg_ok and not socket_ok:
        final_truth = "degraded"
        mismatch_reason = "registry_stale"
    elif process_ok and not socket_ok:
        final_truth = "degraded"
        mismatch_reason = "process_live_without_socket"

    return {
        "registry_truth": {
            "ok": reg_ok,
            "host": reg_host,
            "port": reg_port,
            "pid": pid,
            "source": "endpoint" if endpoint_entry else ("workspace" if workspace_entry else "none"),
        },
        "socket_truth": {"ok": socket_ok, "host": str(host), "port": int(port)},
        "process_truth": {"ok": process_ok, "pid": pid},
        "final_truth": final_truth,
        "mismatch_reason": mismatch_reason,
    }
