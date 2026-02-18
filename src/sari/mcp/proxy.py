"""stdio <-> daemon MCP framed 프록시를 구현한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sari.mcp.daemon_forward_policy import (
    StartDaemonFn,
    build_forward_error_message,
    default_start_daemon,
    forward_with_retry,
    resolve_target,
)
from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.server_daemon_forward import DaemonForwardError, forward_once
from sari.mcp.tool_visibility import filter_tools_list_response_payload, is_hidden_tool_name
from sari.mcp.transport import MCP_MODE_FRAMED, McpTransport, McpTransportParseError


def _is_initialize_request(payload: dict[str, object]) -> bool:
    """payload가 initialize 요청인지 반환한다."""
    return str(payload.get("method", "")).strip() == "initialize"


def _is_tools_list_request(payload: dict[str, object]) -> bool:
    """payload가 tools/list 요청인지 반환한다."""
    return str(payload.get("method", "")).strip() == "tools/list"


def _extract_tools_call_name(payload: dict[str, object]) -> str | None:
    """tools/call 요청에서 도구명을 추출한다."""
    if str(payload.get("method", "")).strip() != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    if normalized == "":
        return None
    return normalized


def _is_draining_response(payload: dict[str, object]) -> bool:
    """daemon 응답이 draining 오류인지 판정한다."""
    error_obj = payload.get("error")
    if not isinstance(error_obj, dict):
        return False
    code_raw = error_obj.get("code")
    message_raw = str(error_obj.get("message", "")).lower()
    if isinstance(code_raw, int) and code_raw == -32001 and "draining" in message_raw:
        return True
    return "draining" in message_raw


def _is_draining_reconnect_enabled() -> bool:
    """draining 자동 재연결 활성화 여부를 반환한다."""
    raw = os.getenv("SARI_MCP_DRAINING_RECONNECT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _retry_after_draining(
    *,
    payload: dict[str, object],
    db_path: Path,
    workspace_root: str | None,
    host_override: str | None,
    port_override: int | None,
    timeout_sec: float,
    initialize_payload: dict[str, object] | None,
) -> dict[str, object]:
    """draining 응답 이후 endpoint 재해석 + initialize 재전송 + 원요청 재시도한다."""
    host_retry, port_retry = resolve_target(db_path, workspace_root, host_override, port_override)
    if initialize_payload is not None and not _is_initialize_request(payload):
        initialize_response = forward_once(initialize_payload, host_retry, port_retry, timeout_sec)
        if _is_draining_response(initialize_response):
            raise ValueError("ERR_DAEMON_DRAINING_RECONNECT_FAILED: initialize replay still draining")
    retried = forward_once(payload, host_retry, port_retry, timeout_sec)
    if _is_draining_response(retried):
        raise ValueError("ERR_DAEMON_DRAINING_RECONNECT_FAILED: request replay still draining")
    return retried


def run_stdio_proxy(
    db_path: Path,
    workspace_root: str | None = None,
    host_override: str | None = None,
    port_override: int | None = None,
    timeout_sec: float = 2.0,
    auto_start_on_failure: bool = True,
    start_daemon_fn: StartDaemonFn | None = None,
) -> int:
    """표준 입출력 MCP 요청을 daemon endpoint로 중계한다."""
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = getattr(sys.stdout, "buffer", sys.stdout)
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream, allow_jsonl=True)
    transport.default_mode = MCP_MODE_FRAMED
    daemon_starter = default_start_daemon if start_daemon_fn is None else start_daemon_fn
    initialize_payload: dict[str, object] | None = None

    while True:
        try:
            read_result = transport.read_message()
        except McpTransportParseError as exc:
            parse_response = McpResponse(request_id=None, result=None, error=McpError(code=-32700, message=str(exc)))
            transport.write_message(parse_response.to_dict(), mode=exc.mode)
            continue

        if read_result is None:
            return 0
        payload, mode = read_result
        request_id = payload.get("id")
        is_notification = request_id is None
        hidden_tool_name = _extract_tools_call_name(payload)
        if hidden_tool_name is not None and is_hidden_tool_name(hidden_tool_name):
            if is_notification:
                continue
            response = McpResponse(
                request_id=request_id,
                result=None,
                error=McpError(code=-32601, message="tool not found"),
            )
            transport.write_message(response.to_dict(), mode=mode)
            continue
        if _is_initialize_request(payload):
            initialize_payload = payload
        try:
            forwarded = forward_with_retry(
                request=payload,
                db_path=db_path,
                workspace_root=workspace_root,
                host_override=host_override,
                port_override=port_override,
                timeout_sec=timeout_sec,
                auto_start_on_failure=auto_start_on_failure,
                start_daemon_fn=daemon_starter,
                resolve_target_fn=resolve_target,
                forward_once_fn=forward_once,
            )
            if _is_draining_reconnect_enabled() and _is_draining_response(forwarded):
                forwarded = _retry_after_draining(
                    payload=payload,
                    db_path=db_path,
                    workspace_root=workspace_root,
                    host_override=host_override,
                    port_override=port_override,
                    timeout_sec=timeout_sec,
                    initialize_payload=initialize_payload,
                )
            if _is_tools_list_request(payload):
                forwarded = filter_tools_list_response_payload(forwarded)
            if is_notification:
                continue
            transport.write_message(forwarded, mode=mode)
        except (DaemonForwardError, OSError, TimeoutError, ValueError) as exc:
            if is_notification:
                continue
            response = McpResponse(
                request_id=request_id,
                result=None,
                error=McpError(code=-32002, message=build_forward_error_message(exc)),
            )
            transport.write_message(response.to_dict(), mode=mode)
