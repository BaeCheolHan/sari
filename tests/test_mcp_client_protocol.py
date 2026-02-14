import io
import json
from unittest.mock import patch

from sari.mcp.cli.mcp_client import ensure_workspace_http, identify_sari_daemon, request_mcp_status


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class _FakeSock:
    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def settimeout(self, _timeout):
        return None

    def sendall(self, data: bytes):
        self.sent += data

    def makefile(self, _mode: str):
        if self._frames:
            return io.BytesIO(self._frames.pop(0))
        return io.BytesIO(b"")


def test_identify_sari_daemon_rejects_too_large_content_length():
    huge = b"Content-Length: 99999999\r\n\r\n{}"
    with patch("socket.create_connection", return_value=_FakeSock([huge, huge])):
        assert identify_sari_daemon("127.0.0.1", 47779, timeout=0.1) is None


def test_ensure_workspace_http_returns_false_on_initialize_error():
    resp = _frame({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "failed"}})
    fake = _FakeSock([resp])
    with patch("socket.create_connection", return_value=fake):
        ok = ensure_workspace_http("127.0.0.1", 47779, workspace_root="/tmp/ws", timeout=0.1)
    assert ok is False


def test_request_mcp_status_returns_tools_call_result():
    init_resp = _frame({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    status_resp = _frame({"jsonrpc": "2.0", "id": 2, "result": {"ok": True, "source": "mcp"}})
    fake = _FakeSock([init_resp, status_resp])
    with patch("socket.create_connection", return_value=fake):
        out = request_mcp_status("127.0.0.1", 47779, workspace_root="/tmp/ws")
    assert out == {"ok": True, "source": "mcp"}


def test_request_mcp_status_returns_none_on_tools_call_error():
    init_resp = _frame({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    err_resp = _frame({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "bad"}})
    fake = _FakeSock([init_resp, err_resp])
    with patch("socket.create_connection", return_value=fake):
        out = request_mcp_status("127.0.0.1", 47779, workspace_root="/tmp/ws")
    assert out is None


def test_request_mcp_status_rejects_non_loopback_host_without_network_call():
    with patch("socket.create_connection", side_effect=AssertionError("should not connect")):
        out = request_mcp_status("10.0.0.7", 47779, workspace_root="/tmp/ws")
    assert out is None


def test_ensure_workspace_http_rejects_non_loopback_host_without_network_call():
    with patch("socket.create_connection", side_effect=AssertionError("should not connect")):
        ok = ensure_workspace_http("10.0.0.7", 47779, workspace_root="/tmp/ws", timeout=0.1)
    assert ok is False
