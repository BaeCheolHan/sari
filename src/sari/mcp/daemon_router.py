"""MCP 데몬 포워딩 라우터를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.daemon_forward_policy import (
    StartDaemonFn,
    build_forward_error_message,
    extract_workspace_root,
    forward_with_retry,
    resolve_target,
    should_forward_to_daemon,
)
from sari.mcp.server_daemon_forward import DaemonForwardError, forward_once


@dataclass(frozen=True)
class DaemonRouterConfig:
    """데몬 라우팅 정책 설정을 표현한다."""

    proxy_to_daemon: bool
    auto_start_on_failure: bool
    timeout_sec: float


class DaemonRouter:
    """MCP 요청의 데몬 포워딩 경로를 캡슐화한다."""

    def __init__(
        self,
        db_path: Path,
        config: DaemonRouterConfig,
        start_daemon_fn: StartDaemonFn,
        resolve_target_fn: Callable[[Path, str | None], tuple[str, int]] = resolve_target,
        forward_once_fn: Callable[[dict[str, object], str, int, float], dict[str, object]] = forward_once,
    ) -> None:
        """라우터 구성에 필요한 의존성을 주입한다."""
        self._db_path = db_path
        self._config = config
        self._start_daemon_fn = start_daemon_fn
        self._resolve_target_fn = resolve_target_fn
        self._forward_once_fn = forward_once_fn

    def should_forward(self, method: str) -> bool:
        """현재 요청을 데몬으로 포워딩해야 하는지 판정한다."""
        return should_forward_to_daemon(proxy_enabled=self._config.proxy_to_daemon, method=method)

    def forward(self, payload: dict[str, object], request_id: object) -> McpResponse:
        """데몬으로 요청을 포워딩하고 JSON-RPC 응답으로 변환한다."""
        workspace_root = extract_workspace_root(payload)
        try:
            forwarded = forward_with_retry(
                request=payload,
                db_path=self._db_path,
                workspace_root=workspace_root,
                host_override=None,
                port_override=None,
                timeout_sec=self._config.timeout_sec,
                auto_start_on_failure=self._config.auto_start_on_failure,
                start_daemon_fn=self._start_daemon_fn,
                resolve_target_fn=self._resolve_target_fn,
                forward_once_fn=self._forward_once_fn,
            )
        except (OSError, TimeoutError, DaemonForwardError, ValueError) as exc:
            return McpResponse(
                request_id=request_id,
                result=None,
                error=McpError(code=-32002, message=build_forward_error_message(exc)),
            )
        response_id = forwarded.get("id", request_id)
        error_payload = forwarded.get("error")
        if isinstance(error_payload, dict):
            code = error_payload.get("code")
            message = error_payload.get("message")
            if isinstance(code, int) and isinstance(message, str):
                return McpResponse(request_id=response_id, result=None, error=McpError(code=code, message=message))
            return McpResponse(
                request_id=response_id,
                result=None,
                error=McpError(code=-32003, message="invalid daemon error response"),
            )
        return McpResponse(request_id=response_id, result=forwarded.get("result"), error=None)
