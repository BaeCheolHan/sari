"""MCP daemon forward framed 통신 유틸을 제공한다."""

from __future__ import annotations

import json
import socket


JsonMap = dict[str, object]


class DaemonForwardError(RuntimeError):
    """데몬 포워딩 실패를 나타낸다."""


def forward_once(request: JsonMap, host: str, port: int, timeout_sec: float) -> JsonMap:
    """단일 MCP 요청을 daemon endpoint로 전달한다."""
    body = json.dumps(request).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    with socket.create_connection((host, port), timeout=timeout_sec) as conn:
        conn.sendall(header + body)
        file_reader = conn.makefile("rb")
        try:
            content_length = _read_content_length(file_reader)
            response_body = file_reader.read(content_length)
            if len(response_body) != content_length:
                raise DaemonForwardError("daemon response body truncated")
            decoded = json.loads(response_body.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise DaemonForwardError("daemon response must be json object")
            return decoded
        finally:
            file_reader.close()


def _read_content_length(file_reader: object) -> int:
    """framed 헤더를 읽고 content-length를 반환한다."""
    headers: dict[str, str] = {}
    header_lines = 0
    while True:
        line = file_reader.readline()
        if not isinstance(line, bytes):
            raise DaemonForwardError("daemon response header read failed")
        if line == b"":
            raise DaemonForwardError("daemon response closed before header")
        try:
            line_str = line.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise DaemonForwardError("daemon response header is not ascii") from exc
        if line_str == "":
            break
        header_lines += 1
        if header_lines > 200:
            raise DaemonForwardError("daemon response header too large")
        if ":" not in line_str:
            continue
        key, value = line_str.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    raw_content_length = headers.get("content-length")
    if raw_content_length is None:
        raise DaemonForwardError("content-length header is missing")
    try:
        content_length = int(raw_content_length)
    except ValueError as exc:
        raise DaemonForwardError("invalid content-length value") from exc
    if content_length <= 0:
        raise DaemonForwardError("content-length must be positive")
    max_content_length = 10 * 1024 * 1024
    if content_length > max_content_length:
        raise DaemonForwardError("daemon response content-length exceeds limit")
    return content_length
