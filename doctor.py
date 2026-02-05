#!/usr/bin/env python3
"""
Sari Doctor - Health Check.
Checks:
1. DB Connection & FTS5
2. Port availability
3. Workspace paths
4. Environment variables
"""
import sys
import os
import socket
import sqlite3
import shutil
import urllib.request
from pathlib import Path

# Add project root to sys.path
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR # The script is already in the repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sari.core.db import LocalSearchDB
from sari.core.workspace import WorkspaceManager
from sari.core.registry import ServerRegistry
try:
    from sari.mcp.cli import get_daemon_address
except ImportError:
    sys.path.insert(0, str(REPO_ROOT))
    from sari.mcp.cli import get_daemon_address

from sari.core.health import SariDoctor

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

def _resolve_version() -> str:
    try:
        from sari.version import __version__
        return __version__
    except Exception:
        v = (os.environ.get("SARI_VERSION") or "").strip()
        return v or "dev"

def run_doctor():
    doctor = SariDoctor()
    print(f"\n{YELLOW}Sari Doctor (v{_resolve_version()}){RESET}")
    print("==================================================")
    print(f"Workspace Root: {doctor.workspace_root}\n")

    doctor.run_all()
    summary = doctor.get_summary()

    for r in summary["results"]:
        status = f"{GREEN}PASS{RESET}" if r["passed"] else (f"{YELLOW}WARN{RESET}" if r["warn"] else f"{RED}FAIL{RESET}")
        msg = f": {r['error']}" if r["error"] else ""
        print(f"[{status}] {r['name']}{msg}")

    print("\n==================================================")
    print("💡 Tip: Run 'init' to setup or 'daemon start' to run.")
    print(f"Run '{sys.executable} install.py' if core modules are missing.")

if __name__ == "__main__":
    run_doctor()
