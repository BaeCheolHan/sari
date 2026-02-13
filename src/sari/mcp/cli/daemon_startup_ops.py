import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional


def _daemon_port_strategy(params: dict[str, object]) -> str:
    raw = (os.environ.get("SARI_DAEMON_PORT_STRATEGY") or "").strip().lower()
    if raw in {"auto", "strict"}:
        return raw
    # Explicitly requested port keeps strict behavior unless overridden.
    if bool(params.get("explicit_port")):
        return "strict"
    return "auto"


def _find_fallback_port(params: dict[str, object], host: str, port: int) -> int:
    registry = params.get("registry")
    try:
        finder = getattr(registry, "find_free_port", None)
        if callable(finder):
            start = max(1, int(port) + 1)
            try:
                return int(finder(host=host, start_port=start))
            except TypeError:
                return int(finder(start))
    except Exception:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def check_port_availability(
    params: dict[str, object],
    *,
    port_in_use: Callable[[str, int], bool],
    smart_kill_port_owner: Callable[[str, int], bool],
    sleep_fn: Callable[[float], None] = time.sleep,
    stderr=sys.stderr,
) -> Optional[int]:
    host = str(params["host"])
    port = int(params["port"])
    strategy = _daemon_port_strategy(params)

    attempts = 8
    for _ in range(attempts):
        if not bool(port_in_use(host, port)):
            return None
        sleep_fn(0.1)

    try:
        if smart_kill_port_owner(host, port):
            if not bool(port_in_use(host, port)):
                return None
    except Exception:
        pass

    if strategy == "auto":
        fallback = _find_fallback_port(params, host, port)
        if fallback != port:
            if bool(port_in_use(host, int(fallback))):
                print(
                    f"❌ Fallback port {fallback} is already in use.",
                    file=stderr,
                )
                return 1
            params["port"] = fallback
            print(
                f"⚠️ Port {port} is in use; falling back to {fallback}.",
                file=stderr,
            )
            return None

    print(f"❌ Port {port} is already in use by another process.", file=stderr)
    return 1


def prepare_daemon_environment(
    params: dict[str, object],
    *,
    get_arg: Callable[[object, str], object],
    runtime_host_key: str,
    runtime_port_key: str,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    args = params["args"]
    workspace_root = str(params["workspace_root"])
    port = int(params["port"])
    repo_root = Path(__file__).parent.parent.parent.resolve()

    env = dict(environ or os.environ)
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["SARI_DAEMON_AUTOSTART"] = "1"
    env["SARI_WORKSPACE_ROOT"] = workspace_root
    env[runtime_port_key] = str(port)

    if get_arg(args, "daemon_host"):
        env[runtime_host_key] = str(get_arg(args, "daemon_host"))
    if get_arg(args, "daemon_port"):
        env[runtime_port_key] = str(get_arg(args, "daemon_port"))
    if get_arg(args, "http_host"):
        env["SARI_HTTP_API_HOST"] = str(get_arg(args, "http_host"))
    if get_arg(args, "http_port") is not None:
        env["SARI_HTTP_API_PORT"] = str(get_arg(args, "http_port"))

    params["env"] = env
    params["repo_root"] = repo_root
    return env


def start_daemon_in_background(
    params: dict[str, object],
    *,
    is_daemon_running: Callable[[str, int], bool],
    popen_factory=subprocess.Popen,
    sleep_fn: Callable[[float], None] = time.sleep,
    stderr=sys.stderr,
) -> int:
    def _reap_child(proc) -> None:
        try:
            proc.wait()
        except Exception:
            pass

    host = str(params["host"])
    port = int(params["port"])
    env = params["env"]
    repo_root = params["repo_root"]

    print(f"Starting daemon on {host}:{port} (background)...")

    sari_root = str(repo_root.parent)
    env["PYTHONPATH"] = f"{sari_root}:{env.get('PYTHONPATH', '')}"

    proc = popen_factory(
        [sys.executable, "-m", "sari.mcp.daemon"],
        cwd=repo_root.parent,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    threading.Thread(target=_reap_child, args=(proc,), daemon=True).start()

    for _ in range(30):
        if is_daemon_running(host, port):
            print(f"✅ Daemon started (PID: {proc.pid})")
            return 0
        sleep_fn(0.1)

    print("❌ Daemon failed to start", file=stderr)
    return 1


def start_daemon_in_foreground(
    params: dict[str, object],
    *,
    get_arg: Callable[[object, str], object],
    runtime_host_key: str,
    runtime_port_key: str,
    daemon_main_provider: Callable[[], Callable[[], object]],
    environ: dict[str, str] | None = None,
) -> int:
    host = str(params["host"])
    port = int(params["port"])
    workspace_root = str(params["workspace_root"])
    args = params["args"]
    repo_root = params["repo_root"]

    print(f"Starting daemon on {host}:{port} (foreground, Ctrl+C to stop)...")

    env = environ if environ is not None else os.environ
    try:
        env["SARI_DAEMON_AUTOSTART"] = "1"
        env["SARI_WORKSPACE_ROOT"] = workspace_root
        env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if get_arg(args, "daemon_host"):
            env[runtime_host_key] = str(get_arg(args, "daemon_host"))
        if get_arg(args, "daemon_port"):
            env[runtime_port_key] = str(get_arg(args, "daemon_port"))
        if get_arg(args, "http_host"):
            env["SARI_HTTP_API_HOST"] = str(get_arg(args, "http_host"))
        if get_arg(args, "http_port") is not None:
            env["SARI_HTTP_API_PORT"] = str(get_arg(args, "http_port"))

        daemon_main = daemon_main_provider()
        asyncio.run(daemon_main())
    except KeyboardInterrupt:
        print("\nDaemon stopped.")

    return 0
