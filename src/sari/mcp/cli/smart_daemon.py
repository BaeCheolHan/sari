import os
import sys
import time
import subprocess
import socket
import logging
from pathlib import Path
from typing import Optional, Tuple, List

try:
    import psutil
except ImportError:
    psutil = None

from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.workspace import WorkspaceManager
from .mcp_client import probe_sari_daemon, ensure_workspace_http

logger = logging.getLogger("sari.smart_daemon")

def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True

def identify_sari_process(proc: "psutil.Process") -> bool:
    """Identify if a process is a Sari process."""
    try:
        # Check process name or command line
        cmdline = " ".join(proc.cmdline()).lower()
        if "sari" in cmdline:
            return True
        # For development or different install methods
        if "python" in cmdline and ("-m sari" in cmdline or "sari/main.py" in cmdline or "sari/mcp/daemon" in cmdline):
            return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return False

def smart_kill_port_owner(host: str, port: int) -> bool:
    """
    Find and kill the process owning the target port if it is a Sari process.
    Returns True if port is now free or was already free.
    """
    if psutil is None:
        logger.warning("psutil not available, skipping smart kill")
        return not is_port_in_use(host, port)

    if not is_port_in_use(host, port):
        return True

    killed = False
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.laddr.port == port:
                    if identify_sari_process(proc):
                        logger.info(f"Smart Kill: Found stale Sari process (PID: {proc.pid}) on port {port}. Terminating...")
                        proc.terminate()
                        # Wait a bit for graceful termination
                        _, alive = psutil.wait_procs([proc], timeout=2)
                        if alive:
                            logger.info(f"Smart Kill: PID {proc.pid} still alive, sending SIGKILL.")
                            proc.kill()
                        killed = True
                        break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            continue
    
    if killed:
        # Give OS a moment to free the port
        time.sleep(0.5)
    
    return not is_port_in_use(host, port)

def ensure_smart_daemon(host: Optional[str] = None, port: Optional[int] = None, workspace_root: Optional[str] = None) -> Tuple[str, int]:
    """
    Ensures a Sari daemon is running. 
    Uses Smart Kill to clear stale processes and Lazy Auto-Start to launch if needed.
    """
    if host is None or port is None:
        host, port = get_daemon_address(workspace_root)
    
    # 1. Check if already running and responsive
    if probe_sari_daemon(host, port):
        # Also ensure workspace is initialized so HTTP server starts
        ensure_workspace_http(host, port, workspace_root)
        return host, port

    # 2. Smart Kill: If port is blocked by a STALE sari process, kill it
    if is_port_in_use(host, port):
        if not smart_kill_port_owner(host, port):
            # Port is still blocked by something NOT sari, or kill failed
            logger.error(f"Port {port} is blocked by a non-Sari process or could not be freed.")
            return host, port

    # 3. Lazy Auto-Start
    logger.info(f"Lazy Auto-Start: Starting daemon on {host}:{port}")
    
    # Go up 3 levels: sari/mcp/cli/smart_daemon.py -> sari/mcp/cli -> sari/mcp -> sari/ -> (repo root)
    repo_root = Path(__file__).parent.parent.parent.parent.resolve()
    
    env = os.environ.copy()
    if workspace_root:
        env["SARI_WORKSPACE_ROOT"] = workspace_root
    # CRITICAL: Force the port for the new daemon
    env["SARI_DAEMON_PORT"] = str(port)
    env["SARI_DAEMON_HOST"] = host
    env["SARI_DAEMON_OVERRIDE"] = "1" # Force resolver to use these
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    
    try:
        subprocess.Popen(
            [sys.executable, "-m", "sari", "daemon", "start", "-d", "--daemon-port", str(port)],
            env=env,
            cwd=repo_root.parent,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.error(f"Failed to launch daemon: {e}")
        return host, port

    # 4. Wait for it to come up
    for _ in range(20):
        if probe_sari_daemon(host, port):
            logger.info("Daemon started and responsive.")
            # Ensure workspace is initialized so HTTP server starts
            ensure_workspace_http(host, port, workspace_root)
            return host, port
        time.sleep(0.5)

    logger.error("Daemon failed to become responsive after start.")
    return host, port
