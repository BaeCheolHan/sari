"""System/log/network related doctor checks."""

from __future__ import annotations

import os
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import TypeAlias

from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import get_registry_path
from sari.mcp.tools.doctor_common import result

DoctorResult: TypeAlias = dict[str, object]
DoctorResults: TypeAlias = list[DoctorResult]


def check_port(port: int, label: str) -> DoctorResult:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return result(f"{label} Port {port} Availability", True)
    except OSError as e:
        return result(f"{label} Port {port} Availability", False, f"Address in use or missing permission: {e}")
    finally:
        try:
            s.close()
        except Exception:
            pass


def check_network() -> DoctorResult:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return result("Network Check", True)
    except OSError as e:
        return result("Network Check", False, f"Unreachable: {e}")


def check_disk_space(ws_root: str, min_gb: float) -> DoctorResult:
    try:
        _, _, free = shutil.disk_usage(ws_root)
        free_gb = free / (1024**3)
        if free_gb < min_gb:
            return result("Disk Space", False, f"Low space: {free_gb:.2f} GB (Min: {min_gb} GB)")
        return result("Disk Space", True)
    except Exception as e:
        return result("Disk Space", False, str(e))


def check_log_errors() -> DoctorResult:
    try:
        env_log_dir = os.environ.get("SARI_LOG_DIR")
        log_dir = Path(env_log_dir).expanduser().resolve() if env_log_dir else WorkspaceManager.get_global_log_dir()
        log_file = log_dir / "daemon.log"
        if not log_file.exists():
            return result("Log Health", True, "No log file yet")

        errors = []
        level_pat = re.compile(r"(?:^|\s-\s)(ERROR|CRITICAL)(?:\s-\s|$)|\[(ERROR|CRITICAL)\]")
        file_size = log_file.stat().st_size
        read_size = min(file_size, 1024 * 1024)

        with open(log_file, "rb") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.splitlines()
            for line in lines[-500:]:
                if level_pat.search(str(line)):
                    errors.append(line.strip())

        if not errors:
            return result("Log Health", True, "No recent errors")

        unique_errs = []
        for e in errors:
            msg = e.split(" - ")[-1] if " - " in e else e
            if msg not in unique_errs:
                unique_errs.append(msg)

        return result("Log Health", False, f"Found {len(errors)} error(s). Symptoms: {', '.join(unique_errs[:3])}")
    except (PermissionError, OSError) as e:
        return result("Log Health", False, f"Log file inaccessible: {e}")
    except Exception as e:
        return result("Log Health", True, f"Scan skipped: {e}", warn=True)


def check_system_env() -> DoctorResults:
    import platform

    results = []
    results.append(result("Platform", True, f"{platform.system()} {platform.release()} ({platform.machine()})"))
    results.append(result("Python", True, sys.version.split()[0]))

    roots = os.environ.get("SARI_WORKSPACE_ROOT")
    results.append(result("Env: SARI_WORKSPACE_ROOT", bool(roots), roots or "Not set"))

    try:
        reg_path = str(get_registry_path())
        results.append(result("Registry Path", True, reg_path))
        if os.path.exists(reg_path):
            if not os.access(reg_path, os.W_OK):
                results.append(result("Registry Access", False, "Registry file is read-only"))
        elif not os.access(os.path.dirname(reg_path), os.W_OK):
            results.append(result("Registry Access", False, "Registry directory is not writable"))
    except Exception as e:
        results.append(result("Registry Path", False, f"Could not determine registry: {e}"))

    return results


def check_process_resources(pid: int) -> DoctorResult:
    try:
        import psutil

        proc = psutil.Process(pid)
        with proc.oneshot():
            mem = proc.memory_info().rss / (1024 * 1024)
            cpu = proc.cpu_percent(interval=0.1)
            return {"mem_mb": round(mem, 1), "cpu_pct": cpu}
    except Exception:
        return {}
