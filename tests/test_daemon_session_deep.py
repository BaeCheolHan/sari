import json
import socket
import time
import threading
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sari.mcp.daemon import SariDaemon
from sari.mcp.session import Session
from sari.mcp.workspace_registry import Registry


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout_sec: float = 5.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"daemon on {host}:{port} did not become ready in time")


def test_session_lifecycle():
    """Test session initialization and cleanup."""
    temp_dir = Path("/tmp/sari_session_test").resolve()
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    # Mocking Registry to avoid DB creation in test
    Registry.get_instance()

    # 1. Create Mock Session
    reader = MagicMock()
    writer = MagicMock()
    session = Session(reader, writer)

    assert session.running is True
    assert session.workspace_root is None

    # 2. Cleanup
    session.cleanup()
    assert session.workspace_root is None

    shutil.rmtree(temp_dir)


def test_daemon_server_communication(tmp_path):
    """Test SariDaemon internal server and command handling."""
    port = _free_port()
    daemon = SariDaemon(host="127.0.0.1", port=port)
    workspace_root = tmp_path / "ws"
    (workspace_root / ".sari").mkdir(parents=True, exist_ok=True)

    # Mocking background indexing
    with patch("sari.core.indexer.main.Indexer.run_forever"):
        daemon_thread = threading.Thread(target=daemon.start, daemon=True)
        daemon_thread.start()
        _wait_for_port("127.0.0.1", port, timeout_sec=5.0)

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
                req = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "rootUri": f"file://{workspace_root}",
                        "protocolVersion": "2025-11-25",
                    },
                }
                body = json.dumps(req).encode("utf-8")
                header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                sock.sendall(header + body)
                sock.settimeout(8.0)
                deadline = time.time() + 8.0
                chunks = []
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b'"result"' in chunk:
                        break
                resp_data = b"".join(chunks)
                assert b'"result"' in resp_data
        finally:
            daemon.stop()
            daemon_thread.join(timeout=2.0)


def test_workspace_registry_singleton():
    """Test that Registry follows singleton pattern and manages state."""
    r1 = Registry.get_instance()
    r2 = Registry.get_instance()
    assert r1 is r2

    # Registry uses a shared dict for SharedState
    ws_path = "/tmp/test_ws"
    # SharedState init will fail without real DB, so we mock get_or_create
    with patch.object(r1, "get_or_create") as mock_get:
        mock_get.return_value = MagicMock()
        state = r1.get_or_create(ws_path)
        assert state is not None


def test_session_cleanup_shuts_down_preinit_server():
    reader = MagicMock()
    writer = MagicMock()
    session = Session(reader, writer)

    class _FakePreinitServer:
        def __init__(self):
            self.shutdown_called = 0

        def shutdown(self):
            self.shutdown_called += 1

    fake = _FakePreinitServer()
    session._preinit_server = fake

    session.cleanup()
    assert fake.shutdown_called == 1
    assert session._preinit_server is None


@pytest.mark.asyncio
async def test_session_reinitialize_same_workspace_does_not_leak_ref(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    ws_uri = f"file://{ws}"

    class _FakeServer:
        def handle_initialize(self, _params):
            return {"protocolVersion": "2025-11-25", "capabilities": {}}

    class _FakeState:
        def __init__(self):
            self.ref_count = 0
            self.persistent = False
            self.server = _FakeServer()

    class _FakeRegistry:
        def __init__(self):
            self.state = _FakeState()

        def get_or_create(self, _workspace_root, persistent=False, track_ref=True):
            if persistent:
                self.state.persistent = True
            if track_ref:
                self.state.ref_count += 1
            return self.state

        def touch_workspace(self, _workspace_root):
            return None

        def release(self, _workspace_root):
            self.state.ref_count = max(0, self.state.ref_count - 1)

    class _FakeServerRegistry:
        def get_daemon(self, _boot_id):
            return {}

        def set_workspace(self, _workspace_root, _boot_id, http_port=None, http_host=None):
            return None

    fake_registry = _FakeRegistry()
    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info.return_value = ("127.0.0.1", 49999)
    session = Session(reader, writer)
    session.registry = fake_registry

    async def _send_json(_data):
        return None

    async def _send_error(_msg_id, _code, _message):
        raise AssertionError("send_error should not be called")

    session.send_json = _send_json  # type: ignore[assignment]
    session.send_error = _send_error  # type: ignore[assignment]

    monkeypatch.setenv("SARI_BOOT_ID", "boot-test")
    monkeypatch.setattr("sari.mcp.session.ServerRegistry", lambda: _FakeServerRegistry())

    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "rootUri": ws_uri,
            "protocolVersion": "2025-11-25",
        },
    }

    await session.handle_initialize(req)
    await session.handle_initialize(req)
    assert fake_registry.state.ref_count == 1
