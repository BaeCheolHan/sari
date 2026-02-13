import io
import json

import pytest

from sari.mcp.server_daemon_forward import forward_over_open_socket


class _Conn:
    def __init__(self):
        self.sent = b""

    def sendall(self, data: bytes):
        self.sent += data


def _trace(*_args, **_kwargs):
    return None


def test_forward_over_open_socket_returns_none_when_content_length_missing():
    conn = _Conn()
    response = b"X-Header: ok\r\n\r\n"
    f = io.BytesIO(response)
    out = forward_over_open_socket(
        request={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        conn=conn,
        f=f,
        trace_fn=_trace,
    )
    assert out is None


def test_forward_over_open_socket_raises_on_invalid_content_length():
    conn = _Conn()
    response = b"Content-Length: nope\r\n\r\n"
    f = io.BytesIO(response)
    with pytest.raises(ValueError):
        forward_over_open_socket(
            request={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            conn=conn,
            f=f,
            trace_fn=_trace,
        )


def test_forward_over_open_socket_raises_on_truncated_body():
    conn = _Conn()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode("utf-8")
    declared = len(body) + 5
    response = f"Content-Length: {declared}\r\n\r\n".encode("ascii") + body
    f = io.BytesIO(response)
    with pytest.raises(ValueError):
        forward_over_open_socket(
            request={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            conn=conn,
            f=f,
            trace_fn=_trace,
        )
