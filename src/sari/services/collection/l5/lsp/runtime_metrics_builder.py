"""SolidLSP 런타임 메트릭 조합 헬퍼."""

from __future__ import annotations


def build_runtime_metrics(
    *,
    hub_metrics: dict[str, int],
    probe_trigger_counts: dict[str, int],
    scope_planner_applied_count: int,
    scope_planner_fallback_index_building_count: int,
    scope_override_hit_count: int,
    runtime_mismatch_auto_recovered_count: int,
    runtime_mismatch_auto_recover_failed_count: int,
    broker_guard_reject_count: int,
    broker_parallelism_guard_skip_count: int,
    document_symbol_sync_skip_requested_count: int,
    document_symbol_sync_skip_accepted_count: int,
    document_symbol_sync_skip_legacy_fallback_count: int,
) -> dict[str, int]:
    """hub/probe/상태 카운터를 MCP 노출용 metrics dict로 조합한다."""
    metrics = dict(hub_metrics)
    for trigger, count in probe_trigger_counts.items():
        metrics[f"probe_trigger_{trigger}_count"] = int(count)
    metrics["scope_planner_applied_count"] = int(scope_planner_applied_count)
    metrics["scope_planner_fallback_index_building_count"] = int(scope_planner_fallback_index_building_count)
    metrics["scope_override_hit_count"] = int(scope_override_hit_count)
    metrics["runtime_mismatch_auto_recovered_count"] = int(runtime_mismatch_auto_recovered_count)
    metrics["runtime_mismatch_auto_recover_failed_count"] = int(runtime_mismatch_auto_recover_failed_count)
    metrics["broker_guard_reject_count"] = int(broker_guard_reject_count)
    metrics["broker_parallelism_guard_skip_count"] = int(broker_parallelism_guard_skip_count)
    metrics["document_symbol_sync_skip_requested_count"] = int(document_symbol_sync_skip_requested_count)
    metrics["document_symbol_sync_skip_accepted_count"] = int(document_symbol_sync_skip_accepted_count)
    metrics["document_symbol_sync_skip_legacy_fallback_count"] = int(document_symbol_sync_skip_legacy_fallback_count)
    metrics.setdefault("session_cache_hit_by_tier_single", 0)
    metrics.setdefault("session_eviction_churn_count", 0)
    metrics.setdefault("lsp_memory_total_rss_mb", 0)
    metrics.setdefault("lsp_memory_pressure_state", 0)
    return metrics
