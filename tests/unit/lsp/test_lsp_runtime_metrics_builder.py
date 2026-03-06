from __future__ import annotations

from sari.services.collection.l5.lsp.runtime_metrics_builder import build_runtime_metrics


def test_build_runtime_metrics_includes_core_and_placeholder_fields() -> None:
    metrics = build_runtime_metrics(
        hub_metrics={"hub_a": 1},
        probe_trigger_counts={"l1": 3},
        scope_planner_applied_count=5,
        scope_planner_fallback_index_building_count=6,
        scope_override_hit_count=7,
        runtime_mismatch_auto_recovered_count=8,
        runtime_mismatch_auto_recover_failed_count=9,
        broker_guard_reject_count=10,
        broker_parallelism_guard_skip_count=11,
        document_symbol_sync_skip_requested_count=12,
        document_symbol_sync_skip_accepted_count=13,
        document_symbol_sync_skip_legacy_fallback_count=14,
        probe_state_backpressure_count=15,
    )

    assert metrics["hub_a"] == 1
    assert metrics["probe_trigger_l1_count"] == 3
    assert metrics["broker_guard_reject_count"] == 10
    assert metrics["probe_state_backpressure_count"] == 15
    assert metrics["session_cache_hit_by_tier_single"] == 0
    assert metrics["session_eviction_churn_count"] == 0
    assert metrics["lsp_memory_total_rss_mb"] == 0
    assert metrics["lsp_memory_pressure_state"] == 0
