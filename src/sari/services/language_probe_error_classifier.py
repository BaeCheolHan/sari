"""Language probe 오류 분류/정규화 유틸리티."""

from __future__ import annotations


def extract_error_code(message: str, default_code: str) -> str:
    """예외 메시지 선두의 ERR_* 코드를 추출한다."""
    trimmed = message.strip()
    if trimmed.startswith("ERR_"):
        code = trimmed.split(":", 1)[0].strip()
        if code != "":
            return code
    return default_code


def is_timeout_error(code: str, message: str) -> bool:
    """오류 코드/메시지가 타임아웃 성격인지 판별한다."""
    timeout_codes = {
        "ERR_LSP_TIMEOUT",
        "ERR_LSP_REQUEST_TIMEOUT",
        "ERR_LSP_DOCUMENT_SYMBOL_TIMEOUT",
    }
    normalized_message = message.strip().lower()
    return (code in timeout_codes) or ("timeout" in normalized_message) or ("timed out" in normalized_message)


def is_recovered_by_restart(message: str) -> bool:
    """메시지에서 재시작 복구 여부 플래그를 탐지한다."""
    normalized_message = message.strip().lower()
    return ("recovered_by_restart" in normalized_message) and ("true" in normalized_message)


def classify_lsp_error_code(code: str, message: str) -> str:
    """LSP 오류를 정책 코드로 정규화한다."""
    normalized_message = message.strip().lower()
    missing_server_tokens = (
        "command not found",
        "no such file",
        "file not found",
        "not installed",
        "missing executable",
        "failed to spawn",
        "failed to start",
        "cannot find",
        "filenotfounderror",
    )
    if any(token in normalized_message for token in missing_server_tokens):
        return "ERR_LSP_SERVER_MISSING"
    if is_timeout_error(code=code, message=message):
        return "ERR_LSP_TIMEOUT"
    return code


def extract_missing_dependency(message: str) -> str | None:
    """예외 메시지에서 누락 의존성 토큰을 추출한다."""
    normalized_message = message.strip()
    if normalized_message == "":
        return None
    lowered = normalized_message.lower()
    if "pyright" in lowered:
        return "pyright"
    if "node" in lowered:
        return "node"
    if "npm" in lowered:
        return "npm"
    if "dotnet" in lowered:
        return "dotnet"
    if "java" in lowered:
        return "java"
    if "no such file" in lowered or "command not found" in lowered or "missing required commands" in lowered:
        return "server_binary"
    return None

