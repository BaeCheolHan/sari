"""MCP stabilization 포트 인터페이스 정의."""

from __future__ import annotations

from typing import Protocol

from sari.mcp.stabilization.stabilization_service import StabilizationPrecheckResult


class StabilizationPort(Protocol):
    """도구 계층에서 사용하는 stabilization 서비스 포트."""

    def apply_soft_limits(self, mode: str, delegated_args: dict[str, object]) -> tuple[dict[str, object], bool, list[str]]:
        ...

    def precheck_read_call(self, arguments: dict[str, object], repo_root: str) -> StabilizationPrecheckResult:
        ...

    def build_read_success_meta(
        self,
        *,
        arguments: dict[str, object],
        repo_root: str,
        mode: str,
        target: str,
        content_text: str,
        read_lines: int,
        read_span: int,
        warnings: list[str],
        degraded: bool,
    ) -> dict[str, object] | None:
        ...

    def build_search_success_meta(
        self,
        *,
        arguments: dict[str, object],
        repo: str,
        query: str,
        items: list[object],
        degraded: bool,
        fatal_error: bool,
        errors: list[dict[str, object]],
    ) -> dict[str, object] | None:
        ...
