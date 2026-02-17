"""MCP 요청/응답 계약 타입을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class McpError:
    """MCP 오류 객체를 표현한다."""

    code: int
    message: str

    def to_dict(self) -> dict[str, object]:
        """오류 객체를 직렬화 가능한 딕셔너리로 변환한다."""
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class McpResponse:
    """MCP JSON-RPC 응답 객체를 표현한다."""

    request_id: object | None
    result: dict[str, object] | None
    error: McpError | None

    def to_dict(self) -> dict[str, object]:
        """응답을 JSON 직렬화 가능한 딕셔너리로 변환한다."""
        base: dict[str, object] = {"jsonrpc": "2.0", "id": self.request_id}
        if self.error is not None:
            base["error"] = self.error.to_dict()
            return base
        base["result"] = self.result if self.result is not None else {}
        return base
