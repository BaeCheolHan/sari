"""L5 admission 런타임 상태 reset/metrics 집계를 담당한다."""

from __future__ import annotations

from sari.core.models import L5RejectReason


class L5RuntimeStatsService:
    """L5 런타임 상태 조작과 metrics 계산 책임을 분리한다."""

    def reset_runtime_state(
        self,
        *,
        reject_counts: dict[L5RejectReason, int],
        cost_units_by_reason: dict[str, float],
        cost_units_by_language: dict[str, float],
        cost_units_by_workspace: dict[str, float],
        admitted_timestamps_by_lang: dict[str, list[float]],
        cooldown_until_by_scope_file: dict[str, float],
    ) -> dict[str, int]:
        for reason in reject_counts:
            reject_counts[reason] = 0
        cost_units_by_reason.clear()
        cost_units_by_language.clear()
        cost_units_by_workspace.clear()
        admitted_timestamps_by_lang.clear()
        cooldown_until_by_scope_file.clear()
        return {
            "l5_total_decisions": 0,
            "l5_total_admitted": 0,
            "l5_batch_decisions": 0,
            "l5_batch_admitted": 0,
        }

    def build_metrics(
        self,
        *,
        total_decisions: int,
        total_admitted: int,
        batch_decisions: int,
        batch_admitted: int,
        reject_counts: dict[L5RejectReason, int],
        cost_units_by_reason: dict[str, float],
        cost_units_by_language: dict[str, float],
        cost_units_by_workspace: dict[str, float],
    ) -> dict[str, float]:
        total_rate = 0.0 if total_decisions <= 0 else float(total_admitted) / float(total_decisions)
        batch_rate = 0.0 if batch_decisions <= 0 else float(batch_admitted) / float(batch_decisions)
        metrics = {
            "l5_total_decisions": float(total_decisions),
            "l5_total_admitted": float(total_admitted),
            "l5_batch_decisions": float(batch_decisions),
            "l5_batch_admitted": float(batch_admitted),
            "l5_call_rate_total_pct": total_rate * 100.0,
            "l5_call_rate_batch_pct": batch_rate * 100.0,
        }
        for reason, count in reject_counts.items():
            metrics[f"l5_reject_count_by_reject_reason_{reason.value}"] = float(count)
        for reason_key, cost_units in cost_units_by_reason.items():
            metrics[f"l5_cost_units_total_by_reason_{reason_key}"] = float(cost_units)
        for language_key, cost_units in cost_units_by_language.items():
            metrics[f"l5_cost_units_total_by_language_{language_key}"] = float(cost_units)
        for workspace_key, cost_units in cost_units_by_workspace.items():
            metrics[f"l5_cost_units_total_by_workspace_{workspace_key}"] = float(cost_units)
        return metrics
