import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import select
import os as _os


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


def _identify(sock: socket.socket) -> dict:
    req = {"jsonrpc": "2.0", "id": 1, "method": "sari/identify"}
    resp = _send_framed(sock, req)
    return resp.get("result") or {}


def _initialize(sock: socket.socket, root: str) -> dict:
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {"rootUri": f"file://{root}", "capabilities": {}},
    }
    last_err = None
    for _ in range(5):
        try:
            resp = _send_framed(sock, req)
            return resp.get("result") or {}
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise last_err if last_err else RuntimeError("initialize failed")


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


def _read_proc_message(proc: subprocess.Popen, timeout: float = 3.0) -> dict:
    stdout = proc.stdout
    if stdout is None:
        raise RuntimeError("stdout not captured")
    fd = stdout.fileno()
    deadline = time.time() + timeout
    buf = b""
    # Read until headers complete
    while b"\r\n\r\n" not in buf:
        if time.time() >= deadline:
            raise RuntimeError("timeout waiting for headers")
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        chunk = _os.read(fd, 4096)
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
        chunk = _os.read(fd, content_length - len(body))
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




def test_multi_clients_and_upgrade_drain(tmp_path):
    registry_path = tmp_path / "server.json"
    ws_a = tmp_path / "wsA"
    ws_b = tmp_path / "wsB"
    ws_a.mkdir()
    ws_b.mkdir()

    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)

    port1 = _free_port()
    port2 = _free_port()

    daemon1 = _start_daemon(base_env, port1)
    try:
        # Multi-client same workspace
        c1 = socket.create_connection((DEFAULT_HOST, port1))
        c2 = socket.create_connection((DEFAULT_HOST, port1))
        try:
            r1 = _initialize(c1, str(ws_a))
            r2 = _initialize(c2, str(ws_a))
            assert r1.get("serverInfo", {}).get("name") == "sari"
            assert r2.get("serverInfo", {}).get("name") == "sari"
        finally:
            c1.close()
            c2.close()

        # Multi-workspace
        c3 = socket.create_connection((DEFAULT_HOST, port1))
        c4 = socket.create_connection((DEFAULT_HOST, port1))
        try:
            r3 = _initialize(c3, str(ws_a))
            r4 = _initialize(c4, str(ws_b))
            assert r3.get("serverInfo", {}).get("name") == "sari"
            assert r4.get("serverInfo", {}).get("name") == "sari"
        finally:
            c3.close()
            c4.close()

        # Upgrade/drain: start daemon2, bind workspace A to it
        daemon2 = _start_daemon(base_env, port2)
        try:
            d1 = socket.create_connection((DEFAULT_HOST, port1))
            d2 = socket.create_connection((DEFAULT_HOST, port2))
            try:
                boot1 = _identify(d1).get("bootId")
                boot2 = _identify(d2).get("bootId")
                assert boot1 and boot2 and boot1 != boot2
                _initialize(d1, str(ws_a))
                _initialize(d2, str(ws_a))
            finally:
                d1.close()
                d2.close()

            # Registry should mark daemon1 draining after ownership change
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            assert data["daemons"][boot1]["draining"] is True
            assert data["workspaces"][str(ws_a.resolve())]["boot_id"] == boot2
        finally:
            _terminate(daemon2)
    finally:
        _terminate(daemon1)


def test_proxy_reconnect_on_daemon_switch(tmp_path):
    registry_path = tmp_path / "server.json"
    ws_a = tmp_path / "wsA"
    ws_a.mkdir()

    base_env = os.environ.copy()
    base_env["SARI_REGISTRY_FILE"] = str(registry_path)
    base_env["SARI_WORKSPACE_ROOT"] = str(ws_a)
    base_env["SARI_INDEXER_MODE"] = "off"
    base_env["SARI_STARTUP_INDEX"] = "0"

    port1 = _free_port()
    port2 = _free_port()

    daemon1 = _start_daemon(base_env, port1)
    try:
        # Seed registry with workspace -> daemon1 mapping
        seed = socket.create_connection((DEFAULT_HOST, port1))
        try:
            _initialize(seed, str(ws_a))
        finally:
            seed.close()

        proxy_env = base_env.copy()
        daemon2 = None
        proxy = subprocess.Popen(
            [sys.executable, "-m", "sari.mcp.proxy"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=proxy_env,
        )
        try:
            init_resp = _send_proc_message_retry(
                proxy,
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "initialize",
                    "params": {"rootUri": f"file://{ws_a}", "capabilities": {}},
                },
            )
            assert init_resp.get("result", {}).get("serverInfo", {}).get("name") == "sari"

            # Start daemon2 and bind workspace to it
            daemon2 = _start_daemon(base_env, port2)
            d2 = socket.create_connection((DEFAULT_HOST, port2))
            try:
                _initialize(d2, str(ws_a))
            finally:
                d2.close()

            # Trigger reconnect by calling initialize again while daemon1 is draining
            resp = _send_proc_message_retry(
                proxy,
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "initialize",
                    "params": {"rootUri": f"file://{ws_a}", "capabilities": {}},
                },
            )
            assert resp.get("result", {}).get("serverInfo", {}).get("name") == "sari"
        finally:
            if daemon2:
                _terminate(daemon2)
            try:
                proxy.terminate()
                proxy.wait(timeout=2)
            except Exception:
                try:
                    proxy.kill()
                except Exception:
                    pass
    finally:
        _terminate(daemon1)
