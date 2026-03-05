"""LSP 프로세스 분류/탐지 유틸리티."""

from __future__ import annotations


def is_residual_lsp_command(command: str) -> bool:
    """잔존 회수 대상 LSP command 여부를 판단한다."""
    lowered = str(command).lower()
    # 안전성 우선: SARI가 관리하는 solidlsp 경로/프로세스로 식별 가능한 경우만 회수한다.
    return ".solidlsp" in lowered or "solidlsp" in lowered


def classify_language_from_command(command: str) -> str:
    """프로세스 command에서 언어 분류 힌트를 추출한다."""
    lowered = str(command).lower()
    if "jdtls" in lowered or "eclipse.jdt.ls" in lowered or "kotlin" in lowered:
        return "java"
    if "typescript-language-server" in lowered or "tsserver" in lowered:
        return "typescript"
    if "pyright" in lowered or "pylsp" in lowered:
        return "python"
    if "gopls" in lowered:
        return "go"
    if "rust-analyzer" in lowered:
        return "rust"
    return "unknown"
