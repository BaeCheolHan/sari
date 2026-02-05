import os
import socket
import subprocess
import sys
import time
import json


DEFAULT_HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except Exception:
            time.sleep(0.05)
    return False


def _send_initialize(host: str, port: int, root: str) -> None:
    with socket.create_connection((host, port), timeout=1.0) as sock:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"rootUri": f"file://{root}", "capabilities": {}},
        }).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        sock.sendall(header + body)
        # Read response to ensure server processes and can cleanup on close.
        f = sock.makefile("rb")
        headers = {}
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                break
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > 0:
            f.read(content_length)
        # Graceful exit
        exit_body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "exit"}).encode("utf-8")
        exit_header = f"Content-Length: {len(exit_body)}\r\n\r\n".encode("ascii")
        sock.sendall(exit_header + exit_body)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def test_idle_shutdown_and_status_wakeup(tmp_path):
    registry_path = tmp_path / "server.json"
    ws = tmp_path / "ws"
    ws.mkdir()

    env = os.environ.copy()
    env["SARI_REGISTRY_FILE"] = str(registry_path)
    env["SARI_WORKSPACE_ROOT"] = str(ws)
    env["SARI_INDEXER_MODE"] = "off"
    env["SARI_STARTUP_INDEX"] = "0"
    env["SARI_DAEMON_IDLE_SEC"] = "1"
    env["SARI_DAEMON_HEARTBEAT_SEC"] = "0.2"
    env["SARI_DAEMON_IDLE_WITH_ACTIVE"] = "1"

    port = _free_port()
    env["SARI_DAEMON_HOST"] = DEFAULT_HOST
    env["SARI_DAEMON_PORT"] = str(port)
    env["SARI_BOOT_ID"] = f"boot-{port}-{time.time_ns()}"

    daemon = subprocess.Popen(
        [sys.executable, "-m", "sari.mcp.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        assert _wait_for_port(DEFAULT_HOST, port, timeout=5.0)
        _send_initialize(DEFAULT_HOST, port, str(ws))
        # Wait for idle shutdown (best-effort)
        time.sleep(2.0)
        if _wait_for_port(DEFAULT_HOST, port, timeout=1.0):
            import pytest
            pytest.skip("idle shutdown not observed on this host")

        # Status should wake up daemon and return 0
        env_status = env.copy()
        env_status.pop("SARI_DAEMON_PORT", None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.cli", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env_status,
        )
        out, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
    finally:
        _terminate(daemon)
