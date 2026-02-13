import os
import signal
import subprocess
import time
from typing import Callable, Optional


def kill_pid_immediate(
    pid: int,
    label: str,
    *,
    os_module=os,
    signal_module=signal,
    time_module=time,
    subprocess_module=subprocess,
) -> bool:
    if not pid:
        return False
    try:
        if os_module.name == "nt":
            subprocess_module.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
            print(f"Executed taskkill for {label} PID {pid}")
            return True
        os_module.kill(pid, signal_module.SIGTERM)
        time_module.sleep(0.15)
        try:
            os_module.kill(pid, 0)
            os_module.kill(pid, signal_module.SIGKILL)
            print(f"Sent SIGKILL to {label} PID {pid}")
        except OSError:
            print(f"Stopped {label} PID {pid}")
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        print(f"Permission denied while stopping {label} PID {pid}")
        return False


def kill_orphan_sari_daemons(
    *,
    detect_orphan_daemons: Callable[[], list[dict[str, object]]],
    kill_pid: Callable[[int, str], bool],
) -> int:
    killed = 0
    for item in detect_orphan_daemons():
        try:
            pid = int(item.get("pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0:
            continue
        if kill_pid(pid, "orphan-daemon"):
            killed += 1
    if killed > 0:
        print(f"🧹 Reaped {killed} orphan daemon process(es).")
    return killed


def kill_orphan_sari_workers(
    host: Optional[str] = None,
    port: Optional[int] = None,
    workspace_root: Optional[str] = None,
    *,
    psutil_module,
    runtime_port_key: str,
    os_module=os,
    getpid: Callable[[], int] = os.getpid,
) -> int:
    del host
    if psutil_module is None:
        return 0

    target_port = int(port) if port is not None else None
    target_root = os_module.path.realpath(workspace_root) if workspace_root else None
    this_pid = getpid()
    killed = 0

    for proc in psutil_module.process_iter(["pid", "ppid", "cmdline", "name"]):
        try:
            info = getattr(proc, "info", {}) or {}
            pid = int(info.get("pid") or 0)
            if pid <= 0 or pid == this_pid:
                continue
            ppid = int(info.get("ppid") or 0)
            if ppid not in {0, 1}:
                continue

            cmdline = info.get("cmdline") or []
            line = " ".join(str(v) for v in cmdline).lower()
            if not line:
                continue
            if "multiprocessing.spawn" not in line and "--multiprocessing-fork" not in line:
                continue

            env = {}
            try:
                env = proc.environ() or {}
            except Exception:
                env = {}

            env_root = str(env.get("SARI_WORKSPACE_ROOT") or "").strip()
            env_port_raw = str(env.get(runtime_port_key) or "").strip()
            env_port = None
            if env_port_raw:
                try:
                    env_port = int(env_port_raw)
                except ValueError:
                    env_port = None

            if target_port is not None and env_port not in {None, target_port}:
                continue
            if target_root and env_root and os_module.path.realpath(env_root) != target_root:
                continue
            if not (env_root or env_port_raw or "sari" in line):
                continue

            proc.terminate()
            try:
                proc.wait(timeout=0.4)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=0.3)
                except Exception:
                    pass
            killed += 1
        except Exception:
            continue

    if killed > 0:
        print(f"🧹 Reaped {killed} orphan worker process(es).")
    return killed


def stop_one_endpoint(
    host: str,
    port: int,
    *,
    is_daemon_running: Callable[[str, int], bool],
    kill_orphan_workers: Callable[..., int],
    remove_pid: Callable[[], None],
    read_pid: Callable[[str, int], Optional[int]],
    registry_factory: Callable[[], object],
    get_registry_targets: Callable[[str, int, Optional[int]], tuple[set[str], set[int]]],
    kill_pid: Callable[[int, str], bool],
    smart_kill_port_owner: Callable[[str, int], bool],
    sleep_fn: Callable[[float], None],
) -> int:
    if not is_daemon_running(host, port):
        kill_orphan_workers(host=host, port=port)
        print("Daemon is not running")
        remove_pid()
        return 0

    pid = read_pid(host, port)
    if not pid:
        try:
            reg = registry_factory()
            inst = reg.resolve_daemon_by_endpoint(str(host), int(port))
            if inst and inst.get("pid"):
                pid = int(inst.get("pid"))
        except Exception:
            pid = None

    boot_ids, http_pids = get_registry_targets(host, port, pid)
    for http_pid in sorted(http_pids):
        kill_pid(http_pid, "http")

    if pid:
        try:
            kill_pid(pid, "daemon")
            for _ in range(10):
                if not is_daemon_running(host, port):
                    break
                sleep_fn(0.1)

            reg = registry_factory()
            for boot_id in boot_ids:
                reg.unregister_daemon(boot_id)

            kill_orphan_workers(host=host, port=port)

            if is_daemon_running(host, port):
                print("⚠️  Daemon port still responds after stop attempt.")
            else:
                print("✅ Daemon stopped")
            return 0
        except (ProcessLookupError, PermissionError):
            print("PID not found or permission denied, daemon may have crashed or locked")
            return 0

    try:
        reg = registry_factory()
        for boot_id in boot_ids:
            reg.unregister_daemon(boot_id)
    except Exception:
        pass
    if smart_kill_port_owner(host, port):
        for _ in range(10):
            if not is_daemon_running(host, port):
                break
            sleep_fn(0.1)
        if not is_daemon_running(host, port):
            remove_pid()
            kill_orphan_workers(host=host, port=port)
            print("✅ Daemon stopped (fallback smart-kill)")
            return 0
    remove_pid()
    kill_orphan_workers(host=host, port=port)
    print("No daemon PID resolved from registry. Cleaned matching registry entries.")
    return 0


def stop_daemon_process(
    params: dict[str, object],
    *,
    kill_orphan_daemons: Callable[[], int],
    list_registry_daemon_endpoints: Callable[[], list[tuple[str, int]]],
    discover_endpoints_from_processes: Callable[[], list[tuple[str, int]]],
    get_daemon_address: Callable[[], tuple[str, int]],
    is_daemon_running: Callable[[str, int], bool],
    kill_orphan_workers: Callable[..., int],
    remove_pid: Callable[[], None],
    stop_one_endpoint: Callable[[str, int], int],
    default_host: str,
    default_port: int,
) -> int:
    if params.get("all"):
        kill_orphan_daemons()
        endpoints = list_registry_daemon_endpoints()
        if not endpoints:
            endpoints = discover_endpoints_from_processes()
        if not endpoints:
            host, port = get_daemon_address()
            if is_daemon_running(host, port):
                endpoints = [(host, port)]
        if not endpoints:
            kill_orphan_workers()
            print("Daemon is not running")
            remove_pid()
            return 0
        rc = 0
        for host, port in endpoints:
            rc = max(rc, stop_one_endpoint(host, port))
        kill_orphan_workers()
        return rc

    host = str(params["host"] or default_host)
    port = int(params["port"] or default_port)
    return stop_one_endpoint(host, port)
