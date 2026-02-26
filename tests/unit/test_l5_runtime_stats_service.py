from __future__ import annotations

from sari.core.models import L5RejectReason
from sari.services.collection.l5_runtime_stats_service import L5RuntimeStatsService


def test_build_metrics_includes_rates_and_breakdowns() -> None:
    service = L5RuntimeStatsService()
    reject_counts = {
        L5RejectReason.PRESSURE_RATE_EXCEEDED: 2,
        L5RejectReason.MODE_NOT_ALLOWED: 1,
    }
    by_reason = {"user_interactive": 7.5}
    by_language = {"java": 10.0}
    by_workspace = {"ws_a": 4.0}

    metrics = service.build_metrics(
        total_decisions=10,
        total_admitted=3,
        batch_decisions=5,
        batch_admitted=1,
        reject_counts=reject_counts,
        cost_units_by_reason=by_reason,
        cost_units_by_language=by_language,
        cost_units_by_workspace=by_workspace,
    )

    assert metrics["l5_total_decisions"] == 10.0
    assert metrics["l5_call_rate_total_pct"] == 30.0
    assert metrics["l5_call_rate_batch_pct"] == 20.0
    assert metrics["l5_reject_count_by_reject_reason_pressure_rate_exceeded"] == 2.0
    assert metrics["l5_reject_count_by_reject_reason_mode_not_allowed"] == 1.0
    assert metrics["l5_cost_units_total_by_reason_user_interactive"] == 7.5
    assert metrics["l5_cost_units_total_by_language_java"] == 10.0
    assert metrics["l5_cost_units_total_by_workspace_ws_a"] == 4.0


def test_reset_runtime_state_resets_mutable_counters() -> None:
    service = L5RuntimeStatsService()
    reject_counts = {reason: 3 for reason in L5RejectReason}
    by_reason = {"a": 1.0}
    by_language = {"java": 2.0}
    by_workspace = {"ws": 3.0}
    admitted_timestamps = {"java": [1.0, 2.0]}
    cooldowns = {"k": 7.0}

    reset = service.reset_runtime_state(
        reject_counts=reject_counts,
        cost_units_by_reason=by_reason,
        cost_units_by_language=by_language,
        cost_units_by_workspace=by_workspace,
        admitted_timestamps_by_lang=admitted_timestamps,
        cooldown_until_by_scope_file=cooldowns,
    )

    assert reset == {
        "l5_total_decisions": 0,
        "l5_total_admitted": 0,
        "l5_batch_decisions": 0,
        "l5_batch_admitted": 0,
    }
    assert all(value == 0 for value in reject_counts.values())
    assert by_reason == {}
    assert by_language == {}
    assert by_workspace == {}
    assert admitted_timestamps == {}
    assert cooldowns == {}
