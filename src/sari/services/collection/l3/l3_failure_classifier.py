"""L3 extract/lease 오류 분류 공용 모듈.

enrich_engine, l3_error_handling_service, 기타 admission/defer 경로에서
동일 분류 규칙을 공유하기 위해 분리했다.
"""

from __future__ import annotations


def extract_error_code_from_lsp_error_message(message: str) -> str:
    """LSP 에러 메시지에서 표준 에러 코드를 추출한다."""
    trimmed = message.strip()
    bracket_start = trimmed.find("[")
    bracket_end = trimmed.find("]", bracket_start + 1)
    if bracket_start >= 0 and bracket_end > bracket_start:
        bracket_code = trimmed[bracket_start + 1 : bracket_end].strip().upper()
        if bracket_code.startswith("ERR_"):
            return bracket_code
    if trimmed.startswith("ERR_"):
        return trimmed.split(":", 1)[0].strip()
    lowered = trimmed.lower()
    if "soft limit" in lowered and "lsp" in lowered:
        return "ERR_LSP_GLOBAL_SOFT_LIMIT"
    if "slot exhausted" in lowered and "lsp" in lowered:
        return "ERR_LSP_SLOT_EXHAUSTED"
    if "workspace contains" in lowered and "no " in lowered and "contains" in lowered:
        return "ERR_LSP_WORKSPACE_MISMATCH"
    if "project model missing" in lowered:
        return "ERR_CONFIG_INVALID"
    if "project not found" in lowered or "no workspace contains" in lowered:
        return "ERR_LSP_DOCUMENT_SYMBOL_FAILED"
    return "ERR_LSP_EXTRACT_FAILED"


def is_scope_escalation_trigger_error_for_l3(*, code: str, message: str) -> bool:
    """scope escalation 대상이 되는 L3 extract 오류인지 판별한다."""
    normalized_code = code.strip().upper()
    if normalized_code in {
        "ERR_LSP_FILE_NOT_IN_SCOPE",
        "ERR_LSP_SCOPE_ROOT_INVALID",
        "ERR_LSP_SCOPE_MISSING_MARKER",
        "ERR_LSP_SCOPE_DISCOVERY_TIMEOUT",
    }:
        return True
    lowered = message.strip().lower()
    if normalized_code == "ERR_LSP_WORKSPACE_MISMATCH":
        return True
    if normalized_code == "ERR_CONFIG_INVALID":
        return True
    if normalized_code == "ERR_LSP_DOCUMENT_SYMBOL_FAILED":
        project_missing_patterns = (
            "no workspace contains",
            "project not found",
            "project model missing",
            "workspace contains",
            "not in scope",
            "scope root",
            "discovery timeout",
            "missing marker",
        )
        return any(pattern in lowered for pattern in project_missing_patterns)
    trigger_markers = (
        "not in scope",
        "scope root",
        "discovery timeout",
        "missing marker",
    )
    if any(marker in lowered for marker in trigger_markers):
        return True
    return False


def next_scope_level_for_l3_escalation(current_scope_level: str | None) -> str | None:
    """module -> repo -> workspace 순으로 escalation 단계 반환."""
    level = (current_scope_level or "module").strip().lower()
    if level == "module":
        return "repo"
    if level == "repo":
        return "workspace"
    return None


def classify_l3_extract_failure_kind(message: str) -> str:
    """L3 extract 오류를 실패 종류로 정규화한다."""
    code = extract_error_code_from_lsp_error_message(message)
    if code in {
        "ERR_LSP_SERVER_MISSING",
        "ERR_LSP_SERVER_SPAWN_FAILED",
        "ERR_RUNTIME_MISMATCH",
        "ERR_CONFIG_INVALID",
        "ERR_LSP_WORKSPACE_MISMATCH",
    }:
        return "PERMANENT_UNAVAILABLE"
    if code in {
        "ERR_RPC_TIMEOUT",
        "ERR_BROKEN_PIPE",
        "ERR_SERVER_EXITED",
        "ERR_LSP_START_TIMEOUT",
        "ERR_LSP_DOCUMENT_SYMBOL_FAILED",
        "ERR_LSP_EXTRACT_FAILED",
    }:
        return "TRANSIENT_FAIL"
    return "TRANSIENT_FAIL"


def extract_broker_lease_reason_from_l3_error(message: str) -> str:
    """ERR_LSP_BROKER_LEASE_REQUIRED 메시지에서 reason 값을 추출한다."""
    lowered = message.strip()
    marker = "reason="
    idx = lowered.find(marker)
    if idx < 0:
        marker = "lease="
        idx = lowered.find(marker)
    if idx < 0:
        return "budget_blocked"
    value = lowered[idx + len(marker) :].strip()
    if "," in value:
        value = value.split(",", 1)[0].strip()
    return value or "budget_blocked"


def map_broker_lease_reason_to_defer_reason(lease_reason: str) -> str:
    """broker lease 거부 이유를 queue defer_reason으로 정규화한다."""
    reason = lease_reason.strip().lower()
    if reason in {"cooldown", "min_lease"}:
        return "broker_defer:cooldown"
    if reason == "starvation_guard":
        return "broker_defer:starvation_guard"
    return "broker_defer:budget"


def broker_defer_delay_seconds_for_reason(lease_reason: str) -> float:
    """broker defer reason별 기본 재평가 지연값."""
    reason = lease_reason.strip().lower()
    if reason in {"cooldown", "min_lease"}:
        return 1.0
    if reason == "starvation_guard":
        return 0.2
    return 0.5
