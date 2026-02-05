import os
import socket
import subprocess
import sys
import time


DEFAULT_HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except Exception:
            time.sleep(0.05)
    return False


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def test_daemon_start_stop_stability(tmp_path):
    registry_path = tmp_path / "server.json"
    env = os.environ.copy()
    env["SARI_REGISTRY_FILE"] = str(registry_path)
    env["SARI_INDEXER_MODE"] = "off"
    env["SARI_STARTUP_INDEX"] = "0"

    for _ in range(3):
        port = _free_port()
        p_env = env.copy()
        p_env["SARI_DAEMON_HOST"] = DEFAULT_HOST
        p_env["SARI_DAEMON_PORT"] = str(port)
        p_env["SARI_BOOT_ID"] = f"boot-{port}-{time.time_ns()}"
        proc = subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=p_env,
        )
        try:
            assert _wait_port(DEFAULT_HOST, port, timeout=5.0)
        finally:
            _terminate(proc)


def test_proxy_start_stop_stability(tmp_path):
    registry_path = tmp_path / "server.json"
    ws = tmp_path / "ws"
    ws.mkdir()

    env = os.environ.copy()
    env["SARI_REGISTRY_FILE"] = str(registry_path)
    env["SARI_WORKSPACE_ROOT"] = str(ws)
    env["SARI_INDEXER_MODE"] = "off"
    env["SARI_STARTUP_INDEX"] = "0"

    # Start a daemon to allow proxy to connect
    port = _free_port()
    d_env = env.copy()
    d_env["SARI_DAEMON_HOST"] = DEFAULT_HOST
    d_env["SARI_DAEMON_PORT"] = str(port)
    d_env["SARI_BOOT_ID"] = f"boot-{port}-{time.time_ns()}"
    daemon = subprocess.Popen(
        [sys.executable, "-m", "sari.mcp.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=d_env,
    )
    try:
        assert _wait_port(DEFAULT_HOST, port, timeout=5.0)
        for _ in range(3):
            p_env = env.copy()
            p_env["SARI_DAEMON_PORT"] = str(port)
            proxy = subprocess.Popen(
                [sys.executable, "-m", "sari.mcp.proxy"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=p_env,
            )
            _terminate(proxy)
    finally:
        _terminate(daemon)


def test_auto_start_stop_stability(tmp_path):
    env = os.environ.copy()
    env["SARI_INDEXER_MODE"] = "off"
    env["SARI_STARTUP_INDEX"] = "0"
    env["SARI_WORKSPACE_ROOT"] = str(tmp_path)
    env["SARI_DAEMON_PORT"] = str(_free_port())
    env["SARI_DAEMON_AUTOSTART"] = "0"

    for _ in range(3):
        proc = subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.cli", "auto"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        _terminate(proc)
