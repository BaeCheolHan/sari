"""read pack1 응답 포맷팅을 담당한다."""

from __future__ import annotations

from sari.mcp.tools.read_executor import ReadExecutionResult
from sari.mcp.tools.tool_common import pack1_items_success


class ReadResponseBuilder:
    """read 실행 결과를 pack1 응답으로 변환한다."""

    def build_success(
        self,
        *,
        execution: ReadExecutionResult,
        warnings_payload: list[dict[str, object]],
        stabilization: dict[str, object] | None,
    ) -> dict[str, object]:
        """성공 응답을 pack1 형식으로 생성한다."""
        return pack1_items_success(
            execution.items,
            cache_hit=execution.cache_hit,
            stabilization=stabilization,
            warnings=warnings_payload,
        )

