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
from sari.core.server_registry import ServerRegistry
from sari.core.workspace import WorkspaceManager
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT
from .mcp_client import probe_sari_daemon, ensure_workspace_http, identify_sari_daemon
from .utils import get_local_version

logger = logging.getLogger("sari.smart_daemon")


def _bg_deploy_enabled() -> bool:
    raw = str(os.environ.get("SARI_BG_DEPLOY", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _prepare_launch_env(host: str, port: int, workspace_root: Optional[str]) -> dict[str, str]:
    import sari

    sari_package_parent = str(Path(sari.__file__).parent.parent.resolve())
    env = os.environ.copy()
    if workspace_root:
        env["SARI_WORKSPACE_ROOT"] = workspace_root
    env[RUNTIME_PORT] = str(port)
    env[RUNTIME_HOST] = host
    env["SARI_DAEMON_OVERRIDE"] = "1"
    existing_pythonpath = env.get("PYTHONPATH", "")
    if sari_package_parent not in existing_pythonpath:
        env["PYTHONPATH"] = sari_package_parent + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    return env


def _launch_daemon(host: str, port: int, workspace_root: Optional[str]) -> bool:
    env = _prepare_launch_env(host, port, workspace_root)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.daemon"],
            env=env,
            cwd=os.getcwd(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error(f"Failed to launch daemon: {e}")
        return False

    for _ in range(20):
        if probe_sari_daemon(host, port, timeout=1.0):
            return True
        time.sleep(0.5)
    return False


def _try_blue_green_upgrade(
    *,
    host: str,
    port: int,
    workspace_root: Optional[str],
    identity: dict,
) -> Optional[Tuple[str, int]]:
    reg = ServerRegistry()
    old_boot = str(identity.get("bootId") or "")
    if not old_boot:
        try:
            inst = reg.resolve_daemon_by_endpoint(host, int(port)) or {}
            old_boot = str(inst.get("boot_id") or "")
        except Exception:
            old_boot = ""
    try:
        candidate_port = int(reg.find_free_port(host=host, start_port=max(1, int(port) + 1)))
    except Exception:
        candidate_port = int(port) + 1
    if candidate_port == int(port):
        candidate_port = int(port) + 1

    if not _launch_daemon(host, candidate_port, workspace_root):
        return None

    cand_ident = identify_sari_daemon(host, candidate_port) or {}
    candidate_boot = str(cand_ident.get("bootId") or "")
    if not candidate_boot:
        return None

    dep = reg.begin_deploy(candidate_boot, expected_active_boot_id=old_boot or None)
    generation = int(dep.get("generation") or 0)
    if generation <= 0:
        return None

    reg.mark_candidate_healthy(generation, candidate_boot)
    switched = reg.switch_active(generation, candidate_boot)
    if str(switched.get("active_boot_id") or "") != candidate_boot:
        return None
    if old_boot:
        reg.set_daemon_draining(old_boot, True)

    def _rollback_candidate(reason: str) -> Tuple[str, int]:
        reg.record_health_failure(generation, candidate_boot, reason=reason)
        reg.record_health_failure(generation, candidate_boot, reason=reason)
        reg.record_health_failure(generation, candidate_boot, reason=reason)
        reg.rollback_active(generation, old_boot, reason=reason)
        if old_boot:
            reg.set_daemon_draining(old_boot, False)
        try:
            from . import cmd_daemon_stop

            cmd_daemon_stop(argparse.Namespace(daemon_host=host, daemon_port=candidate_port))
        except Exception:
            pass
        return (host, int(port))

    if not ensure_workspace_http(host, candidate_port, workspace_root):
        return _rollback_candidate("workspace_attach_failed")

    fail_streak = 0
    for _ in range(3):
        if probe_sari_daemon(host, candidate_port, timeout=1.0):
            fail_streak = 0
        else:
            fail_streak += 1
            reg.record_health_failure(generation, candidate_boot, reason="post_switch_probe_failed")
        if fail_streak >= 3:
            return _rollback_candidate("post_switch_probe_failed_x3")
        time.sleep(0.2)

    return (host, candidate_port)


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
            if _bg_deploy_enabled() and (not draining):
                switched = _try_blue_green_upgrade(
                    host=host,
                    port=port,
                    workspace_root=workspace_root,
                    identity=identity,
                )
                if switched is not None:
                    return switched
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
    if _launch_daemon(host, port, workspace_root):
        logger.info("Daemon started and responsive.")
        ensure_workspace_http(host, port, workspace_root)
        return host, port

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
