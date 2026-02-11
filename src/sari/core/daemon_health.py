import os
from typing import Any

try:
    import psutil
except Exception:
    psutil = None


def _get_registry_daemon_pids() -> set[int]:
    try:
        from sari.core.server_registry import ServerRegistry

        reg = ServerRegistry()
        data = reg._load()
        pids: set[int] = set()
        for info in (data.get("daemons") or {}).values():
            if not isinstance(info, dict):
                continue
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
            except Exception:
                continue
            pids.add(pid)
        return pids
    except Exception:
        return set()


def _is_sari_daemon_cmdline(cmdline: list[str]) -> bool:
    parts = [str(v).strip().lower() for v in cmdline if str(v).strip()]
    if not parts:
        return False
    line = " ".join(parts)

    # Accept only real daemon runtime commandlines.
    if "sari.mcp.daemon" in parts:
        return True
    if "-m sari.mcp.daemon" in line:
        return True
    if "sari/mcp/daemon.py" in line or "sari\\mcp\\daemon.py" in line:
        return True
    return False


def detect_orphan_daemons() -> list[dict[str, Any]]:
    """
    Return running Sari daemon processes that are not present in server registry.
    """
    if psutil is None:
        return []

    registered_pids = _get_registry_daemon_pids()
    out: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or 0)
            cmdline = list(proc.info.get("cmdline") or [])
            if pid <= 0 or not cmdline:
                continue
            if not _is_sari_daemon_cmdline(cmdline):
                continue
            if pid in registered_pids:
                continue
            out.append({"pid": pid, "cmdline": " ".join(cmdline)})
        except Exception:
            continue
    return out
