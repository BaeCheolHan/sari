import argparse
import os
import signal
import subprocess
import sys
import time
from typing import Optional, Set, Tuple, TypeAlias

try:
    import psutil
except ImportError:
    psutil = None

from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address
from sari.core.daemon_runtime_state import RUNTIME_HOST, RUNTIME_PORT
from sari.core.workspace import WorkspaceManager
from sari.mcp.server_registry import ServerRegistry

from .daemon_lifecycle import (
    extract_daemon_start_params as _extract_daemon_start_params_impl,
    extract_daemon_stop_params as _extract_daemon_stop_params_impl,
    needs_upgrade_or_drain as _needs_upgrade_or_drain_impl,
)
from .daemon_orchestration_ops import handle_existing_daemon as _handle_existing_daemon_impl
from .daemon_process_ops import (
    kill_orphan_sari_daemons as _kill_orphan_sari_daemons_impl,
    kill_orphan_sari_workers as _kill_orphan_sari_workers_impl,
    kill_pid_immediate as _kill_pid_immediate_impl,
    stop_daemon_process as _stop_daemon_process_impl,
    stop_one_endpoint as _stop_one_endpoint_impl,
)
from .daemon_registry_ops import (
    discover_daemon_endpoints_from_processes as _discover_daemon_endpoints_from_processes_impl,
    get_registry_targets as _get_registry_targets_impl,
    list_registry_daemon_endpoints as _list_registry_daemon_endpoints_impl,
    list_registry_daemons as _list_registry_daemons_impl,
)
from .daemon_startup_ops import (
    check_port_availability as _check_port_availability_impl,
    prepare_daemon_environment as _prepare_daemon_environment_impl,
    start_daemon_in_background as _start_daemon_in_background_impl,
    start_daemon_in_foreground as _start_daemon_in_foreground_impl,
)
from .mcp_client import identify_sari_daemon, probe_sari_daemon
from .smart_daemon import ensure_smart_daemon, smart_kill_port_owner
from .utils import get_local_version, get_pid_file_path

DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT

_cmd_daemon_start_func = None

DaemonRow: TypeAlias = dict[str, object]; DaemonParams: TypeAlias = dict[str, object]; DaemonRows: TypeAlias = list[DaemonRow]

def set_daemon_start_function(func):
    global _cmd_daemon_start_func
    _cmd_daemon_start_func = func

def is_daemon_running(host: str, port: int) -> bool:
    return probe_sari_daemon(host, port, timeout=1.0)

def read_pid(host: Optional[str] = None, port: Optional[int] = None) -> Optional[int]:
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
    pid_file = WorkspaceManager.get_global_data_dir() / "daemon.pid"
    for path in (get_pid_file_path(), pid_file):
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
    start_func = _cmd_daemon_start_func
    if start_func is None:
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
    return _needs_upgrade_or_drain_impl(identify, local_version=get_local_version())

def ensure_daemon_running(
    daemon_host: str,
    daemon_port: int,
    http_host: str = "",
    http_port: Optional[int] = None,
    allow_upgrade: bool = False,
) -> Tuple[str, int, bool]:
    del http_host, http_port, allow_upgrade
    host, port = ensure_smart_daemon(daemon_host, daemon_port)
    return host, port, True

def extract_daemon_start_params(args: argparse.Namespace) -> DaemonParams:
    return _extract_daemon_start_params_impl(
        args,
        workspace_root_resolver=WorkspaceManager.resolve_workspace_root,
        registry_factory=ServerRegistry,
        daemon_address_resolver=get_daemon_address,
        default_host=DEFAULT_HOST,
        default_port=DEFAULT_PORT,
    )

def handle_existing_daemon(params: DaemonParams) -> Optional[int]:
    from . import cmd_daemon_stop

    return _handle_existing_daemon_impl(
        params,
        kill_orphan_daemons=kill_orphan_sari_daemons,
        identify_daemon=identify_sari_daemon,
        needs_upgrade_or_drain=needs_upgrade_or_drain,
        read_pid=read_pid,
        stop_daemon=cmd_daemon_stop,
    )

def kill_orphan_sari_daemons() -> int:
    try:
        from sari.core.daemon_health import detect_orphan_daemons
    except Exception:
        return 0
    return _kill_orphan_sari_daemons_impl(
        detect_orphan_daemons=detect_orphan_daemons,
        kill_pid=kill_pid_immediate,
    )

def check_port_availability(params: DaemonParams) -> Optional[int]:
    from .utils import is_port_in_use as port_in_use

    return _check_port_availability_impl(
        params,
        port_in_use=port_in_use,
        smart_kill_port_owner=smart_kill_port_owner,
        sleep_fn=time.sleep,
        stderr=sys.stderr,
    )

def prepare_daemon_environment(params: DaemonParams) -> dict[str, str]:
    from .utils import get_arg as _arg

    return _prepare_daemon_environment_impl(
        params,
        get_arg=_arg,
        runtime_host_key=RUNTIME_HOST,
        runtime_port_key=RUNTIME_PORT,
        environ=os.environ.copy(),
    )

def start_daemon_in_background(params: DaemonParams) -> int:
    return _start_daemon_in_background_impl(
        params,
        is_daemon_running=is_daemon_running,
        popen_factory=subprocess.Popen,
        sleep_fn=time.sleep,
        stderr=sys.stderr,
    )

def start_daemon_in_foreground(params: DaemonParams) -> int:
    from .utils import get_arg as _arg

    return _start_daemon_in_foreground_impl(
        params,
        get_arg=_arg,
        runtime_host_key=RUNTIME_HOST,
        runtime_port_key=RUNTIME_PORT,
        daemon_main_provider=lambda: __import__("sari.mcp.daemon", fromlist=["main"]).main,
        environ=os.environ,
    )

def kill_pid_immediate(pid: int, label: str) -> bool:
    return _kill_pid_immediate_impl(
        pid,
        label,
        os_module=os,
        signal_module=signal,
        time_module=time,
        subprocess_module=subprocess,
    )

def get_registry_targets(host: str, port: int, pid_hint: Optional[int]) -> Tuple[Set[str], Set[int]]:
    return _get_registry_targets_impl(
        host,
        port,
        pid_hint,
        registry_factory=ServerRegistry,
        default_host=DEFAULT_HOST,
    )

def list_registry_daemons() -> DaemonRows:
    return _list_registry_daemons_impl(
        registry_factory=ServerRegistry,
        kill_probe=lambda pid: os.kill(pid, 0),
        default_host=DEFAULT_HOST,
    )

def list_registry_daemon_endpoints() -> list[Tuple[str, int]]:
    return _list_registry_daemon_endpoints_impl(
        rows_provider=list_registry_daemons,
        default_host=DEFAULT_HOST,
    )

def _discover_daemon_endpoints_from_processes() -> list[Tuple[str, int]]:
    return _discover_daemon_endpoints_from_processes_impl(
        psutil_module=psutil,
        probe_daemon=probe_sari_daemon,
    )

def extract_daemon_stop_params(args: argparse.Namespace) -> DaemonParams:
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
