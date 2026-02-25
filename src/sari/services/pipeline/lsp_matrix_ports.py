"""Pipeline LSP matrix 관련 포트(Protocol) 정의."""

from __future__ import annotations

from typing import Protocol


class LanguageProbePort(Protocol):
    """LSP 언어 probe 실행 포트."""

    def run(self, repo_root: str) -> dict[str, object]:
        """레포 루트 기준 probe 결과를 반환한다."""
        ...


class PipelineLspMatrixPort(Protocol):
    """LSP 매트릭스 실행/조회 포트."""

    def run(
        self,
        repo_root: str,
        required_languages: tuple[str, ...] | None = None,
        fail_on_unavailable: bool = True,
        strict_all_languages: bool = True,
        strict_symbol_gate: bool = True,
    ) -> dict[str, object]:
        """LSP 매트릭스를 실행한다."""
        ...

    def get_latest_report(self, repo_root: str) -> dict[str, object]:
        """최신 LSP 매트릭스 리포트를 조회한다."""
        ...
