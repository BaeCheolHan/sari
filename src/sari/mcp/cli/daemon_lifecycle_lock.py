import os
import sys
from pathlib import Path
from typing import Callable

from filelock import FileLock, Timeout

from sari.core.workspace import WorkspaceManager


def _lock_path() -> Path:
    return WorkspaceManager.get_global_data_dir() / "daemon.lifecycle.lock"


def _lock_timeout_sec() -> float:
    raw = str(os.environ.get("SARI_DAEMON_LIFECYCLE_LOCK_TIMEOUT", "8") or "8").strip()
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 8.0


def run_with_lifecycle_lock(
    operation: str,
    action: Callable[[], int],
    *,
    stderr=None,
) -> int:
    err = stderr or sys.stderr
    lock = FileLock(str(_lock_path()), timeout=_lock_timeout_sec())
    try:
        with lock:
            return int(action())
    except Timeout:
        print(
            f"❌ Another daemon lifecycle operation is in progress. retry operation={operation}",
            file=err,
        )
        return 1
