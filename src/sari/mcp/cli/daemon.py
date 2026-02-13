"""
Daemon lifecycle management for Sari CLI.

This module handles daemon process management including starting, stopping,
and checking daemon status.
"""

import os
import sys
import time
import signal
import argparse
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple, Set, TypeAlias

try:
    import psutil
except ImportError:
    psutil = None

from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import ServerRegistry
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT

from .utils import get_pid_file_path, get_local_version
from .mcp_client import identify_sari_daemon, probe_sari_daemon
from .smart_daemon import ensure_smart_daemon, smart_kill_port_owner
from .daemon_lifecycle import (
    extract_daemon_start_params as _extract_daemon_start_params_impl,
    extract_daemon_stop_params as _extract_daemon_stop_params_impl,
    needs_upgrade_or_drain as _needs_upgrade_or_drain_impl,
)
from .daemon_registry_ops import (
    discover_daemon_endpoints_from_processes as _discover_daemon_endpoints_from_processes_impl,
    get_registry_targets as _get_registry_targets_impl,
    list_registry_daemon_endpoints as _list_registry_daemon_endpoints_impl,
    list_registry_daemons as _list_registry_daemons_impl,
)
from .daemon_process_ops import (
    kill_orphan_sari_daemons as _kill_orphan_sari_daemons_impl,
    kill_orphan_sari_workers as _kill_orphan_sari_workers_impl,
    kill_pid_immediate as _kill_pid_immediate_impl,
    stop_daemon_process as _stop_daemon_process_impl,
    stop_one_endpoint as _stop_one_endpoint_impl,
)

DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT

# Optional injection point kept for backward compatibility in tests.
_cmd_daemon_start_func = None

DaemonRow: TypeAlias = dict[str, object]
DaemonParams: TypeAlias = dict[str, object]
DaemonRows: TypeAlias = list[DaemonRow]


def set_daemon_start_function(func):
    """
    Set the daemon start command function.
    
    This is used to avoid circular imports between daemon.py and daemon_commands.py.
    
    Args:
        func: The cmd_daemon_start function
    """
    global _cmd_daemon_start_func
    _cmd_daemon_start_func = func


def is_daemon_running(host: str, port: int) -> bool:
    """
    Check if a Sari daemon is running on the given port.
    
    Args:
        host: Daemon host
        port: Daemon port
    
    Returns:
        True if daemon is running, False otherwise
    """
    return probe_sari_daemon(host, port, timeout=1.0)


def read_pid(host: Optional[str] = None, port: Optional[int] = None) -> Optional[int]:
    """
    Read daemon PID from registry (single source of truth).
    
    Args:
        host: Optional daemon host
        port: Optional daemon port
    
    Returns:
        PID if found, None otherwise
    """
    try:
        reg = ServerRegistry()
        if host and port:
            inst = reg.resolve_daemon_by_endpoint(str(host), int(port))
            if inst and inst.get("pid"):
                return int(inst.get("pid"))
        workspace_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
        ws_inst = reg.resolve_workspace_daemon(str(workspace_root))
        if ws_inst and ws_inst.get("pid"):
            return int(ws_inst.get("pid"))
    except Exception:
        pass
    return None


def remove_pid() -> None:
    """
    Legacy cleanup only; daemon state is stored in server.json.
    
    Removes old PID files for backward compatibility.
    """
    PID_FILE = WorkspaceManager.get_global_data_dir() / "daemon.pid"
    for path in (get_pid_file_path(), PID_FILE):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def build_start_args(
    daemonize: bool = True,
    daemon_host: str = "",
    daemon_port: Optional[int] = None,
    http_host: str = "",
    http_port: Optional[int] = None,
) -> argparse.Namespace:
    """
    Build argument namespace for daemon start command.
    
    Args:
        daemonize: Whether to daemonize the process
        daemon_host: Daemon host address
        daemon_port: Daemon port number
        http_host: HTTP server host address
        http_port: HTTP server port number
    
    Returns:
        Argument namespace
    """
    return argparse.Namespace(
        daemonize=daemonize,
        daemon_host=daemon_host,
        daemon_port=daemon_port,
        http_host=http_host,
        http_port=http_port,
    )


def start_daemon_background(
    daemon_host: str = "",
    daemon_port: Optional[int] = None,
    http_host: str = "",
    http_port: Optional[int] = None,
) -> bool:
    """
    Start daemon in background mode.
    
    Args:
        daemon_host: Daemon host address
        daemon_port: Daemon port number
        http_host: HTTP server host address
        http_port: HTTP server port number
    
    Returns:
        True if daemon started successfully, False otherwise
    """
    start_func = _cmd_daemon_start_func
    if start_func is None:
        # Lazy import to avoid eager circular coupling at package import time.
        from .commands.daemon_commands import cmd_daemon_start
        start_func = cmd_daemon_start
    
    start_args = build_start_args(
        daemonize=True,
        daemon_host=daemon_host,
        daemon_port=daemon_port,
        http_host=http_host,
        http_port=http_port,
    )
    return start_func(start_args) == 0


def needs_upgrade_or_drain(identify: Optional[dict]) -> bool:
    """
    Check if daemon needs upgrade or is draining.
    
    Args:
        identify: Identify payload from daemon
    
    Returns:
        True if upgrade needed or draining, False otherwise
    """
    return _needs_upgrade_or_drain_impl(identify, local_version=get_local_version())


def ensure_daemon_running(
    daemon_host: str,
    daemon_port: int,
    http_host: str = "",
    http_port: Optional[int] = None,
    allow_upgrade: bool = False,
) -> Tuple[str, int, bool]:
    host, port = ensure_smart_daemon(daemon_host, daemon_port)
    return host, port, True
def extract_daemon_start_params(args: argparse.Namespace) -> DaemonParams:
    """Extract and validate daemon start parameters."""
    return _extract_daemon_start_params_impl(
        args,
        workspace_root_resolver=WorkspaceManager.resolve_workspace_root,
        registry_factory=ServerRegistry,
        daemon_address_resolver=get_daemon_address,
        default_host=DEFAULT_HOST,
        default_port=DEFAULT_PORT,
    )


def handle_existing_daemon(params: DaemonParams) -> Optional[int]:
    """Handle existing daemon instance, return exit code if should exit early."""
    # Always reap stale/orphan daemon processes first so new start path is clean.
    kill_orphan_sari_daemons()
    
    host = params["host"]
    port = params["port"]
    workspace_root = params["workspace_root"]
    registry = params["registry"]
    explicit_port = params["explicit_port"]
    force_start = params["force_start"]
    params["args"]
    
    identify = identify_sari_daemon(host, port)
    if not identify:
        return None  # No existing daemon, continue
    
    # Handle explicit port conflicts
    if explicit_port:
        ws_inst = registry.resolve_workspace_daemon(str(workspace_root))
        same_instance = bool(ws_inst and int(ws_inst.get("port", 0)) == int(port))
        if not same_instance:
            # Requested explicit port is occupied by another daemon instance.
            from . import cmd_daemon_stop
            stop_args = argparse.Namespace(daemon_host=host, daemon_port=port)
            cmd_daemon_stop(stop_args)
            identify = identify_sari_daemon(host, port)
            if identify:
                print(f"❌ Port {port} is occupied by another running daemon.", file=sys.stderr)
                return 1
    
    # Check if we need to upgrade or if daemon is already running
    if not force_start and not needs_upgrade_or_drain(identify):
        pid = read_pid(host, port)
        print(f"✅ Daemon already running on {host}:{port}")
        if pid:
            print(f"   PID: {pid}")
        return 0

    # Strict singleton policy: replace existing daemon at the same endpoint.
    from . import cmd_daemon_stop
    stop_args = argparse.Namespace(daemon_host=host, daemon_port=port)
    cmd_daemon_stop(stop_args)
    identify = identify_sari_daemon(host, port)
    if identify:
        print(f"❌ Failed to replace existing daemon on {host}:{port}.", file=sys.stderr)
        return 1
    return None


def kill_orphan_sari_daemons() -> int:
    """
    Kill running Sari daemon processes not tracked by current server registry.

    Returns:
        Number of orphan daemon processes terminated.
    """
    try:
        from sari.core.daemon_health import detect_orphan_daemons
    except Exception:
        return 0
    return _kill_orphan_sari_daemons_impl(
        detect_orphan_daemons=detect_orphan_daemons,
        kill_pid=kill_pid_immediate,
    )


def check_port_availability(params: DaemonParams) -> Optional[int]:
    """Check if port is available, return exit code if should exit early."""
    from .utils import is_port_in_use as port_in_use
    
    host = params["host"]
    port = params["port"]
    params["explicit_port"]
    params["registry"]
    
    # stop/replace 직후에는 소켓 정리 타이밍 때문에 짧게 EADDRINUSE가 튈 수 있어
    # 제한된 재시도 후 최종 판단한다.
    attempts = 8
    last_in_use = False
    for _ in range(attempts):
        last_in_use = bool(port_in_use(host, port))
        if not last_in_use:
            return None
        time.sleep(0.1)

    # 포트가 여전히 점유되어 있으면 stale Sari 프로세스 회수 1회 시도
    try:
        if smart_kill_port_owner(host, port):
            if not port_in_use(host, port):
                return None
    except Exception:
        pass

    print(f"❌ Port {port} is already in use by another process.", file=sys.stderr)
    return 1


def prepare_daemon_environment(params: DaemonParams) -> dict[str, str]:
    """Prepare environment variables for daemon process."""
    from .utils import get_arg as _arg
    
    args = params["args"]
    workspace_root = params["workspace_root"]
    port = params["port"]
    
    # Go up 3 levels: sari/mcp/cli.py -> sari/mcp -> sari/ -> (repo root)
    repo_root = Path(__file__).parent.parent.parent.resolve()
    
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["SARI_DAEMON_AUTOSTART"] = "1"
    env["SARI_WORKSPACE_ROOT"] = workspace_root
    env[RUNTIME_PORT] = str(port)
    
    if _arg(args, "daemon_host"):
        env[RUNTIME_HOST] = _arg(args, "daemon_host")
    if _arg(args, "daemon_port"):
        env[RUNTIME_PORT] = str(_arg(args, "daemon_port"))
    if _arg(args, "http_host"):
        env["SARI_HTTP_API_HOST"] = _arg(args, "http_host")
    if _arg(args, "http_port") is not None:
        env["SARI_HTTP_API_PORT"] = str(_arg(args, "http_port"))
    
    params["env"] = env
    params["repo_root"] = repo_root
    return env


def start_daemon_in_background(params: DaemonParams) -> int:
    """Start daemon process in background."""
    def _reap_child(proc: subprocess.Popen) -> None:
        try:
            proc.wait()
        except Exception:
            pass
    
    host = params["host"]
    port = params["port"]
    env = params["env"]
    repo_root = params["repo_root"]
    
    print(f"Starting daemon on {host}:{port} (background)...")
    
    # --- ENRICH ENVIRONMENT ---
    sari_root = str(repo_root.parent)
    env["PYTHONPATH"] = f"{sari_root}:{env.get('PYTHONPATH', '')}"

    proc = subprocess.Popen(
        [sys.executable, "-m", "sari.mcp.daemon"],
        cwd=repo_root.parent,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    # Reap child when it exits to prevent zombie processes in long-lived callers.
    threading.Thread(target=_reap_child, args=(proc,), daemon=True).start()

    # Wait for startup
    for _ in range(30):
        if is_daemon_running(host, port):
            print(f"✅ Daemon started (PID: {proc.pid})")
            return 0
        time.sleep(0.1)

    print("❌ Daemon failed to start", file=sys.stderr)
    return 1


def start_daemon_in_foreground(params: DaemonParams) -> int:
    """Start daemon process in foreground."""
    from .utils import get_arg as _arg
    
    host = params["host"]
    port = params["port"]
    workspace_root = params["workspace_root"]
    args = params["args"]
    repo_root = params["repo_root"]
    
    print(f"Starting daemon on {host}:{port} (foreground, Ctrl+C to stop)...")

    try:
        # Import and run directly
        os.environ["SARI_DAEMON_AUTOSTART"] = "1"
        os.environ["SARI_WORKSPACE_ROOT"] = workspace_root
        os.environ["PYTHONPATH"] = str(repo_root) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
        if _arg(args, "daemon_host"):
            os.environ[RUNTIME_HOST] = _arg(args, "daemon_host")
        if _arg(args, "daemon_port"):
            os.environ[RUNTIME_PORT] = str(_arg(args, "daemon_port"))
        if _arg(args, "http_host"):
            os.environ["SARI_HTTP_API_HOST"] = _arg(args, "http_host")
        if _arg(args, "http_port") is not None:
            os.environ["SARI_HTTP_API_PORT"] = str(_arg(args, "http_port"))
        from sari.mcp.daemon import main as daemon_main
        import asyncio
        asyncio.run(daemon_main())
    except KeyboardInterrupt:
        print("\nDaemon stopped.")

    return 0


# --- Stop Daemon Helpers ---

def kill_pid_immediate(pid: int, label: str) -> bool:
    """
    Forcefully kill a process by PID.
    
    Args:
        pid: Process ID
        label: Label for logging
    
    Returns:
        True if kill command sent, False on permission error
    """
    return _kill_pid_immediate_impl(
        pid,
        label,
        os_module=os,
        signal_module=signal,
        time_module=time,
        subprocess_module=subprocess,
    )


def get_registry_targets(host: str, port: int, pid_hint: Optional[int]) -> Tuple[Set[str], Set[int]]:
    """
    Identify target boot IDs and HTTP PIDs from registry.
    
    Args:
        host: Target host
        port: Target port
        pid_hint: Optional PID hint to filter targets
    
    Returns:
        Tuple of (boot_ids, http_pids)
    """
    return _get_registry_targets_impl(
        host,
        port,
        pid_hint,
        registry_factory=ServerRegistry,
        default_host=DEFAULT_HOST,
    )


def list_registry_daemons() -> DaemonRows:
    """List all live daemon entries from registry."""
    return _list_registry_daemons_impl(
        registry_factory=ServerRegistry,
        kill_probe=lambda pid: os.kill(pid, 0),
        default_host=DEFAULT_HOST,
    )


def list_registry_daemon_endpoints() -> list[Tuple[str, int]]:
    """List unique live daemon endpoints from registry."""
    return _list_registry_daemon_endpoints_impl(
        rows_provider=list_registry_daemons,
        default_host=DEFAULT_HOST,
    )


def _discover_daemon_endpoints_from_processes() -> list[Tuple[str, int]]:
    """
    Best-effort fallback discovery when registry is stale.
    Scans local processes for Sari daemon listeners and verifies them via MCP ping.
    """
    return _discover_daemon_endpoints_from_processes_impl(
        psutil_module=psutil,
        probe_daemon=probe_sari_daemon,
    )


def extract_daemon_stop_params(args: argparse.Namespace) -> DaemonParams:
    """Extract stop parameters from args."""
    return _extract_daemon_stop_params_impl(
        args,
        default_host=DEFAULT_HOST,
        default_port=DEFAULT_PORT,
    )


def kill_orphan_sari_workers(
    host: Optional[str] = None,
    port: Optional[int] = None,
    workspace_root: Optional[str] = None,
) -> int:
    """
    Reap orphaned multiprocessing workers that were spawned by Sari daemon/indexer.

    Args:
        host: Reserved for future filtering.
        port: Optional daemon port filter based on worker env.
        workspace_root: Optional workspace filter based on worker env.

    Returns:
        Number of worker processes terminated.
    """
    return _kill_orphan_sari_workers_impl(
        host,
        port,
        workspace_root,
        psutil_module=psutil,
        runtime_port_key=RUNTIME_PORT,
        os_module=os,
        getpid=os.getpid,
    )


def stop_one_endpoint(host: str, port: int) -> int:
    """Stop daemon and related HTTP process for one endpoint."""
    return _stop_one_endpoint_impl(
        host,
        port,
        is_daemon_running=is_daemon_running,
        kill_orphan_workers=kill_orphan_sari_workers,
        remove_pid=remove_pid,
        read_pid=read_pid,
        registry_factory=ServerRegistry,
        get_registry_targets=get_registry_targets,
        kill_pid=kill_pid_immediate,
        smart_kill_port_owner=smart_kill_port_owner,
        sleep_fn=time.sleep,
    )


def stop_daemon_process(params: DaemonParams) -> int:
    """Stop daemon process(es) and cleanup."""
    return _stop_daemon_process_impl(
        params,
        kill_orphan_daemons=kill_orphan_sari_daemons,
        list_registry_daemon_endpoints=list_registry_daemon_endpoints,
        discover_endpoints_from_processes=_discover_daemon_endpoints_from_processes,
        get_daemon_address=get_daemon_address,
        is_daemon_running=is_daemon_running,
        kill_orphan_workers=kill_orphan_sari_workers,
        remove_pid=remove_pid,
        stop_one_endpoint=stop_one_endpoint,
        default_host=DEFAULT_HOST,
        default_port=DEFAULT_PORT,
    )
