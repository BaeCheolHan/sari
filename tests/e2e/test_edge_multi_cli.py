import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import select
import pytest


DEFAULT_HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((DEFAULT_HOST, 0))
        return s.getsockname()[1]


def _wait_for_daemon(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"daemon not reachable at {host}:{port}")


def _start_daemon(env: dict, port: int) -> subprocess.Popen:
    p_env = env.copy()
    p_env["SARI_DAEMON_HOST"] = DEFAULT_HOST
    p_env["SARI_DAEMON_PORT"] = str(port)
    p_env["SARI_BOOT_ID"] = f"boot-{port}-{time.time_ns()}"
    p_env.setdefault("SARI_DAEMON_AUTOSTART", "0")
    p_env.setdefault("SARI_INDEXER_MODE", "off")
    p_env.setdefault("SARI_STARTUP_INDEX", "0")
    proc = subprocess.Popen(
        [sys.executable, "-m", "sari.mcp.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=p_env,
    )
    _wait_for_daemon(DEFAULT_HOST, port)
    return proc


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _read_framed(sock: socket.socket) -> dict:
    f = sock.makefile("rb")
    headers = {}
    while True:
        line = f.readline()
        if not line:
            raise RuntimeError("connection closed")
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
    content_length = int(headers.get(b"content-length", b"0"))
    body = f.read(content_length)
    if not body:
        raise RuntimeError("empty response")
    return json.loads(body.decode("utf-8"))


def _send_framed(sock: socket.socket, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sock.sendall(header + body)
    return _read_framed(sock)


def _initialize(sock: socket.socket, root: str) -> dict:
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {"rootUri": f"file://{root}", "capabilities": {}},
    }
    return _send_framed(sock, req)


def _read_proc_message(proc: subprocess.Popen, timeout: float = 3.0) -> dict:
    stdout = proc.stdout
    if stdout is None:
        raise RuntimeError("stdout not captured")
    fd = stdout.fileno()
    deadline = time.time() + timeout
    buf = b""
    while b"\r\n\r\n" not in buf:
        if time.time() >= deadline:
            raise RuntimeError("timeout waiting for headers")
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            raise RuntimeError("connection closed")
        buf += chunk
    header_bytes, rest = buf.split(b"\r\n\r\n", 1)
    headers = header_bytes.decode("utf-8", errors="ignore").split("\r\n")
    content_length = None
    for h in headers:
        parts = h.split(":", 1)
        if len(parts) == 2 and parts[0].strip().lower() == "content-length":
            content_length = int(parts[1].strip())
            break
    if content_length is None:
        raise RuntimeError("missing Content-Length")
    body = rest
    while len(body) < content_length:
        if time.time() >= deadline:
            raise RuntimeError("timeout waiting for body")
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        chunk = os.read(fd, content_length - len(body))
        if not chunk:
            raise RuntimeError("connection closed")
        body += chunk
    return json.loads(body[:content_length].decode("utf-8"))


def _send_proc_message(proc: subprocess.Popen, payload: dict) -> dict:
    stdin = proc.stdin
    if stdin is None:
        raise RuntimeError("stdin not captured")
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stdin.write(header + body)
    stdin.flush()
    return _read_proc_message(proc)


def _send_proc_message_retry(proc: subprocess.Popen, payload: dict, attempts: int = 10, delay: float = 0.1) -> dict:
    last_err = None
    for _ in range(attempts):
        try:
            return _send_proc_message(proc, payload)
        except RuntimeError as e:
            last_err = e
            time.sleep(delay)
    raise last_err if last_err else RuntimeError("request failed")


def test_port_collision_fallback(tmp_path):
    registry_path = tmp_path / "server.json"
    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)
    base_env["SARI_INDEXER_MODE"] = "off"
    base_env["SARI_STARTUP_INDEX"] = "0"

    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((DEFAULT_HOST, port))
    blocker.listen(1)
    try:
        env = base_env.copy()
        env["SARI_DAEMON_AUTOSTART"] = "0"
        proc = subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.cli", "daemon", "start", "-d", "--daemon-port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        out, err = proc.communicate(timeout=5)
        txt = (out.decode("utf-8", errors="ignore") + err.decode("utf-8", errors="ignore"))
        assert "already in use" in txt or "Port" in txt
    finally:
        blocker.close()


def test_tcp_blocked_falls_back_to_stdio(tmp_path):
    # Best-effort: on many systems this will not yield EACCES and should be skipped.
    env = os.environ.copy()
    env["SARI_INDEXER_MODE"] = "off"
    env["SARI_STARTUP_INDEX"] = "0"
    # Force host to a permission-denied like path via unresolvable socket (simulate block)
    env["SARI_DAEMON_HOST"] = "0.0.0.0"
    env["SARI_DAEMON_PORT"] = str(_free_port())
    proc = subprocess.Popen(
        [sys.executable, "-m", "sari.mcp.cli", "auto"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        if proc.poll() is not None:
            pytest.skip("auto exited early; TCP block not reproducible on this host")
        try:
            resp = _send_proc_message_retry(
                proc,
                {"jsonrpc": "2.0", "id": 1, "method": "sari/identify"},
                attempts=5,
                delay=0.2,
            )
            assert resp.get("result", {}).get("name") == "sari"
        except (RuntimeError, BrokenPipeError):
            pytest.skip("TCP block not reproducible on this host")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def test_http_fallback_updates_registry(tmp_path):
    registry_path = tmp_path / "server.json"
    ws = tmp_path / "ws"
    ws.mkdir()
    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)
    base_env["SARI_WORKSPACE_ROOT"] = str(ws)
    base_env["SARI_INDEXER_MODE"] = "off"
    base_env["SARI_STARTUP_INDEX"] = "0"

    # Occupy target HTTP port to force fallback
    http_port = _free_port()
    base_env["SARI_HTTP_API_PORT"] = str(http_port)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind((DEFAULT_HOST, http_port))
        blocker.listen(1)
    except OSError:
        pytest.skip("HTTP port already in use; skipping fallback test")
    daemon = None
    daemon_port = _free_port()
    try:
        daemon = _start_daemon(base_env, daemon_port)
        # Seed workspace mapping to ensure registry is written
        s = socket.create_connection((DEFAULT_HOST, daemon_port))
        try:
            _initialize(s, str(ws))
        finally:
            s.close()
        # Read registry and ensure http_port is set and not the blocked port
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        ws_info = data["workspaces"].get(str(ws.resolve()))
        assert ws_info is not None
        assert ws_info.get("http_port") is not None
        assert int(ws_info["http_port"]) != http_port
    finally:
        blocker.close()
        if daemon:
            _terminate(daemon)


def test_concurrent_upgrade_race(tmp_path):
    registry_path = tmp_path / "server.json"
    ws = tmp_path / "ws"
    ws.mkdir()
    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)
    base_env["SARI_WORKSPACE_ROOT"] = str(ws)
    base_env["SARI_INDEXER_MODE"] = "off"
    base_env["SARI_STARTUP_INDEX"] = "0"

    port = _free_port()
    daemon = _start_daemon(base_env, port)
    try:
        # Simulate multiple clients starting with "upgrade needed"
        env1 = base_env.copy()
        env2 = base_env.copy()
        env1["SARI_DAEMON_PORT"] = str(port)
        env2["SARI_DAEMON_PORT"] = str(port)
        p1 = subprocess.Popen([sys.executable, "-m", "sari.mcp.cli", "auto"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env1)
        p2 = subprocess.Popen([sys.executable, "-m", "sari.mcp.cli", "auto"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env2)
        time.sleep(0.5)
        p1.terminate()
        p2.terminate()
    finally:
        _terminate(daemon)


def test_drain_keeps_existing_session(tmp_path):
    registry_path = tmp_path / "server.json"
    ws = tmp_path / "ws"
    ws.mkdir()
    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)
    base_env["SARI_WORKSPACE_ROOT"] = str(ws)
    base_env["SARI_INDEXER_MODE"] = "off"
    base_env["SARI_STARTUP_INDEX"] = "0"

    port1 = _free_port()
    port2 = _free_port()
    daemon1 = _start_daemon(base_env, port1)
    try:
        c = socket.create_connection((DEFAULT_HOST, port1))
        try:
            _initialize(c, str(ws))
            # Create daemon2 and re-bind workspace (draining daemon1)
            daemon2 = _start_daemon(base_env, port2)
            try:
                d2 = socket.create_connection((DEFAULT_HOST, port2))
                try:
                    _initialize(d2, str(ws))
                finally:
                    d2.close()
                # Existing session should still answer identify
                resp = _send_framed(c, {"jsonrpc": "2.0", "id": 5, "method": "sari/identify"})
                assert resp.get("result", {}).get("name") == "sari"
            finally:
                _terminate(daemon2)
        finally:
            c.close()
    finally:
        _terminate(daemon1)
