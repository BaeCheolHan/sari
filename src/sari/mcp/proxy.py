"""stdio <-> daemon MCP framed 프록시를 구현한다."""

from __future__ import annotations

import sys
from pathlib import Path

from sari.core.daemon_resolver import resolve_daemon_address
from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.server_daemon_forward import DaemonForwardError, forward_once
from sari.mcp.transport import MCP_MODE_FRAMED, McpTransport, McpTransportParseError


def run_stdio_proxy(
    db_path: Path,
    workspace_root: str | None = None,
    host_override: str | None = None,
    port_override: int | None = None,
    timeout_sec: float = 2.0,
) -> int:
    """표준 입출력 MCP 요청을 daemon endpoint로 중계한다."""
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = getattr(sys.stdout, "buffer", sys.stdout)
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream, allow_jsonl=True)
    transport.default_mode = MCP_MODE_FRAMED

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
        try:
            host, port = _resolve_target(
                db_path=db_path,
                workspace_root=workspace_root,
                host_override=host_override,
                port_override=port_override,
            )
            forwarded = forward_once(request=payload, host=host, port=port, timeout_sec=timeout_sec)
            transport.write_message(forwarded, mode=mode)
        except (DaemonForwardError, OSError, TimeoutError, ValueError) as exc:
            response = McpResponse(
                request_id=request_id,
                result=None,
                error=McpError(code=-32002, message=f"proxy forward failed: {exc}"),
            )
            transport.write_message(response.to_dict(), mode=mode)


def _resolve_target(
    db_path: Path,
    workspace_root: str | None,
    host_override: str | None,
    port_override: int | None,
) -> tuple[str, int]:
    """프록시 대상 daemon endpoint를 결정한다."""
    if host_override is not None and host_override.strip() != "" and port_override is not None:
        return host_override.strip(), port_override
    return resolve_daemon_address(db_path=db_path, workspace_root=workspace_root)

