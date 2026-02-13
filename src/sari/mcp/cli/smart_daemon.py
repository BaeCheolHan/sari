import os
import sys
import time
import argparse
import subprocess
import socket
import logging
import signal
from pathlib import Path
from typing import Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None

from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.workspace import WorkspaceManager
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT
from .mcp_client import probe_sari_daemon, ensure_workspace_http, identify_sari_daemon
from .utils import get_local_version

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
        if "python" in cmdline and (
                "-m sari" in cmdline or "sari/main.py" in cmdline or "sari/mcp/daemon" in cmdline):
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
            # On macOS, net_connections() often requires higher privileges.
            # We try it, but prepare for fallback.
            connections = proc.net_connections(kind="inet")
            for conn in connections:
                if conn.laddr.port == port:
                    if identify_sari_process(proc):
                        logger.info(
                            f"Smart Kill: Found stale Sari process (PID: {proc.pid}) on port {port}. Terminating...")
                        proc.terminate()
                        _, alive = psutil.wait_procs([proc], timeout=2)
                        if alive:
                            proc.kill()
                        killed = True
                        break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            continue

    # Fallback for macOS/Linux using lsof if psutil failed to find/kill the
    # owner
    if not killed and is_port_in_use(host, port):
        try:
            import subprocess
            # Find PID using lsof
            result = subprocess.run(["lsof",
                                     "-t",
                                     f"-i:{port}",
                                     "-sTCP:LISTEN"],
                                    capture_output=True,
                                    text=True,
                                    check=False)
            pids = result.stdout.strip().split()
            for pid_str in pids:
                pid = int(pid_str)
                p = psutil.Process(pid)
                if identify_sari_process(p):
                    logger.info(
                        f"Smart Kill (lsof): Found stale Sari process (PID: {pid}) on port {port}. Terminating...")
                    p.terminate()
                    _, alive = psutil.wait_procs([p], timeout=2)
                    if alive:
                        p.kill()
                    killed = True
        except Exception as e:
            logger.debug("lsof fallback failed: %s", e)

    if killed:
        # Give OS a moment to free the port
        time.sleep(0.5)

    return not is_port_in_use(host, port)


def ensure_smart_daemon(host: Optional[str] = None,
                        port: Optional[int] = None,
                        workspace_root: Optional[str] = None) -> Tuple[str,
                                                                       int]:
    """
    Ensures a Sari daemon is running.
    Uses Smart Kill to clear stale processes and Lazy Auto-Start to launch if needed.
    """
    if host is None or port is None:
        host, port = get_daemon_address(workspace_root)

    _reap_orphan_daemons()

    # 1. Check if already running and responsive
    identity = identify_sari_daemon(host, port)
    if identity:
        existing_version = str(identity.get("version") or "")
        local_version = str(get_local_version() or "")
        draining = bool(identity.get("draining"))
        needs_replace = bool(
            draining or (
                existing_version and local_version and existing_version != local_version))
        if needs_replace:
            try:
                from . import cmd_daemon_stop
                cmd_daemon_stop(
                    argparse.Namespace(
                        daemon_host=host,
                        daemon_port=port))
            except Exception as e:
                logger.error(
                    f"Failed to stop stale daemon before upgrade: {e}")
                return host, port
            if identify_sari_daemon(host, port):
                logger.error(
                    f"Daemon at {host}:{port} still responds after stop attempt.")
                return host, port
        else:
            # If it's a Sari daemon, we don't kill it even if the root is different.
            # Sari daemons can manage multiple workspaces.
            # We just need to ensure the current workspace is initialized
            # within it.
            current_root = workspace_root or WorkspaceManager.resolve_workspace_root()
            ensure_workspace_http(host, port, current_root)
            return host, port

    # 2. Smart Kill: If port is blocked by a STALE sari process (that didn't
    # respond to identify), kill it
    if is_port_in_use(host, port):
        if not smart_kill_port_owner(host, port):
            # Port is still blocked by something NOT sari, or kill failed
            logger.error(
                f"Port {port} is blocked by a non-Sari process or could not be freed.")
            return host, port

    # 3. Lazy Auto-Start
    logger.info(f"Lazy Auto-Start: Starting daemon on {host}:{port}")

    # Ensure the daemon uses the same sari package as the current process
    import sari
    sari_package_parent = str(Path(sari.__file__).parent.parent.resolve())

    env = os.environ.copy()
    if workspace_root:
        env["SARI_WORKSPACE_ROOT"] = workspace_root
    # CRITICAL: Force the port for the new daemon
    env[RUNTIME_PORT] = str(port)
    env[RUNTIME_HOST] = host
    env["SARI_DAEMON_OVERRIDE"] = "1"  # Force resolver to use these

    # Prepend the current sari package location to PYTHONPATH to ensure
    # version consistency
    existing_pythonpath = env.get("PYTHONPATH", "")
    if sari_package_parent not in existing_pythonpath:
        env["PYTHONPATH"] = sari_package_parent + \
            (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    try:
        subprocess.Popen([sys.executable,
                          "-m",
                          "sari",
                          "daemon",
                          "start",
                          "-d",
                          "--daemon-port",
                          str(port)],
                         env=env,
                         cwd=os.getcwd(),
                         start_new_session=True,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.error(f"Failed to launch daemon: {e}")
        return host, port

    # 4. Wait for it to come up
    for _ in range(20):
        if probe_sari_daemon(host, port, timeout=1.0):
            logger.info("Daemon started and responsive.")
            # Ensure workspace is initialized so HTTP server starts
            ensure_workspace_http(host, port, workspace_root)
            return host, port
        time.sleep(0.5)

    logger.error("Daemon failed to become responsive after start.")
    return host, port


def _reap_orphan_daemons() -> int:
    """
    Best-effort orphan-daemon cleanup for lazy auto-start path.
    """
    try:
        from sari.core.daemon_health import detect_orphan_daemons
    except Exception:
        return 0

    killed = 0
    for item in detect_orphan_daemons():
        try:
            pid = int(item.get("pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0 or pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.15)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            killed += 1
        except ProcessLookupError:
            continue
        except Exception:
            continue

    if killed > 0:
        logger.info("Reaped %d orphan daemon process(es) before auto-start.", killed)
    return killed
