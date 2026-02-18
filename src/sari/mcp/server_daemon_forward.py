"""MCP daemon forward framed 통신 유틸을 제공한다."""

from __future__ import annotations

import json
import http.client


JsonMap = dict[str, object]


class DaemonForwardError(RuntimeError):
    """데몬 포워딩 실패를 나타낸다."""


def forward_once(request: JsonMap, host: str, port: int, timeout_sec: float) -> JsonMap:
    """단일 MCP 요청을 daemon endpoint로 전달한다."""
    body = json.dumps(request).encode("utf-8")
    connection = http.client.HTTPConnection(host, port, timeout=timeout_sec)
    try:
        connection.request("POST", "/mcp", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response_body = response.read()
    finally:
        connection.close()
    if response.status < 200 or response.status >= 300:
        raise DaemonForwardError(f"daemon http status={response.status}")
    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DaemonForwardError("daemon response must be json object") from exc
    if not isinstance(decoded, dict):
        raise DaemonForwardError("daemon response must be json object")
    return decoded
