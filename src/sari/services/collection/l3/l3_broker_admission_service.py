"""L3 broker lease 거부 해석 서비스."""

from __future__ import annotations


class L3BrokerAdmissionService:
    """L3 extract 오류에서 broker lease 거부 의미를 정규화한다."""

    def is_broker_lease_denial(self, error_message: str) -> bool:
        return "ERR_LSP_BROKER_LEASE_REQUIRED" in error_message

    def extract_lease_reason(self, error_message: str) -> str:
        marker = "reason="
        idx = error_message.find(marker)
        if idx < 0:
            return "budget_blocked"
        value = error_message[idx + len(marker) :].strip()
        if "," in value:
            value = value.split(",", 1)[0].strip()
        return value or "budget_blocked"

    def map_defer_reason(self, lease_reason: str) -> str:
        reason = lease_reason.strip().lower()
        if reason in {"cooldown", "min_lease"}:
            return "broker_defer:cooldown"
        if reason == "starvation_guard":
            return "broker_defer:starvation_guard"
        return "broker_defer:budget"

    def defer_delay_seconds(self, lease_reason: str) -> float:
        reason = lease_reason.strip().lower()
        if reason in {"cooldown", "min_lease"}:
            return 1.0
        if reason == "starvation_guard":
            return 0.2
        return 0.5

