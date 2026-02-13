"""Daemon forward/socket helper functions for MCP server."""

from __future__ import annotations

import json
import os
from typing import Callable, Optional


JsonMap = dict[str, object]

_MAX_HEADER_LINES = 200
_MAX_HEADER_BYTES = 64 * 1024
_DEFAULT_MAX_CONTENT_LENGTH = 10 * 1024 * 1024


def ensure_daemon_connection(
    *,
    tid: int,
    daemon_channels_lock: object,
    daemon_channels: dict[int, object],
    daemon_address: tuple[str, int],
    timeout_sec: float,
    trace_fn: Callable[..., None],
    create_connection_fn: Callable[..., object],
) -> tuple[object, object]:
    with daemon_channels_lock:
        ch = daemon_channels.get(tid)
        if ch is not None:
            trace_fn("daemon_connection_reuse", tid=tid)
            return ch
    trace_fn("daemon_connection_new", tid=tid, daemon_address=daemon_address)
    conn = create_connection_fn(daemon_address, timeout=timeout_sec)
    f = conn.makefile("rb")
    with daemon_channels_lock:
        daemon_channels[tid] = (conn, f)
    return conn, f


def close_daemon_connection(
    *,
    tid: int,
    daemon_channels_lock: object,
    daemon_channels: dict[int, object],
    trace_fn: Callable[..., None],
) -> None:
    with daemon_channels_lock:
        ch = daemon_channels.pop(tid, None)
    if not ch:
        return
    trace_fn("daemon_connection_close", tid=tid)
    conn, f = ch
    try:
        f.close()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def close_all_daemon_connections(
    *,
    daemon_channels_lock: object,
    daemon_channels: dict[int, object],
    trace_fn: Callable[..., None],
) -> None:
    with daemon_channels_lock:
        items = list(daemon_channels.items())
        daemon_channels.clear()
    trace_fn("daemon_connections_close_all", count=len(items))
    for _tid, (conn, f) in items:
        try:
            f.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def forward_over_open_socket(
    *,
    request: JsonMap,
    conn: object,
    f: object,
    trace_fn: Callable[..., None],
) -> Optional[JsonMap]:
    body = json.dumps(request).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    conn.sendall(header + body)
    trace_fn(
        "daemon_socket_sent",
        msg_id=request.get("id"),
        method=request.get("method"),
        bytes=len(body),
    )

    headers: dict[bytes, bytes] = {}
    header_lines = 0
    header_bytes = 0
    while True:
        line = f.readline()
        if not line:
            break
        header_lines += 1
        header_bytes += len(line)
        if header_lines > _MAX_HEADER_LINES or header_bytes > _MAX_HEADER_BYTES:
            raise ValueError("daemon response header too large")
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()

    raw_length = headers.get(b"content-length", b"0")
    try:
        content_length = int(raw_length)
    except Exception as exc:
        raise ValueError("invalid content-length header") from exc
    if content_length <= 0:
        trace_fn("daemon_socket_no_content", msg_id=request.get("id"))
        return None
    try:
        max_content_length = int(
            os.environ.get("SARI_DAEMON_MAX_CONTENT_LENGTH", str(_DEFAULT_MAX_CONTENT_LENGTH))
            or str(_DEFAULT_MAX_CONTENT_LENGTH)
        )
    except Exception:
        max_content_length = _DEFAULT_MAX_CONTENT_LENGTH
    if content_length > max_content_length:
        raise ValueError("daemon response content-length exceeds limit")
    resp_body = f.read(content_length)
    if not resp_body:
        trace_fn("daemon_socket_no_body", msg_id=request.get("id"))
        return None
    if len(resp_body) != content_length:
        raise ValueError("daemon response body truncated")
    resp = json.loads(resp_body.decode("utf-8"))
    trace_fn("daemon_socket_received", msg_id=request.get("id"), bytes=content_length)
    return resp


def forward_error_response(request: JsonMap, error_message: str) -> Optional[JsonMap]:
    msg_id = request.get("id")
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32002,
            "message": f"Failed to forward to daemon: {error_message}. Try 'sari daemon start'.",
        },
    } if msg_id is not None else None
