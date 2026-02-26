from __future__ import annotations

import queue

from sari.core.models import L5RejectReason
from sari.services.collection.enrich_runtime_service_registry import EnrichRuntimeServiceRegistry


class _EngineStub:
    def __init__(self) -> None:
        self._lsp_backend = object()
        self._l3_parallel_enabled = True
        self._l3_executor_max_workers = 4
        self._l3_backpressure_on_interactive = True
        self._l3_backpressure_cooldown_sec = 0.5
        self._enrich_queue_repo = object()
        self._error_policy = object()
        self._l3_supported_languages = set()
        self._l3_recent_success_ttl_sec = 5
        self._readiness_repo = object()
        self._lsp_probe_l1_languages = set()
        self._l3_ready_queue = queue.Queue()
        self._policy_repo = None
        self._file_repo = object()
        self._l5_reject_counts_by_reason = {reason: 0 for reason in L5RejectReason}
        self._l5_cost_units_by_reason = {}
        self._l5_cost_units_by_language = {}
        self._l5_cost_units_by_workspace = {}
        self._l5_admitted_timestamps_by_lang = {}
        self._l5_cooldown_until_by_scope_file = {}


def test_registry_returns_cached_instances_per_service_type() -> None:
    engine = _EngineStub()
    registry = EnrichRuntimeServiceRegistry(engine)

    assert registry.l3_scheduling_service() is registry.l3_scheduling_service()
    assert registry.l3_error_handling_service() is registry.l3_error_handling_service()
    assert registry.l3_skip_runtime_service() is registry.l3_skip_runtime_service()
    assert registry.l3_runtime_coordination_service() is registry.l3_runtime_coordination_service()
    assert registry.l3_bootstrap_mode_service() is registry.l3_bootstrap_mode_service()
    assert registry.l5_runtime_stats_service() is registry.l5_runtime_stats_service()


def test_registry_reset_l5_runtime_state_mutates_engine_maps() -> None:
    engine = _EngineStub()
    for key in list(engine._l5_reject_counts_by_reason):
        engine._l5_reject_counts_by_reason[key] = 2
    engine._l5_cost_units_by_reason["x"] = 1.0
    engine._l5_cost_units_by_language["python"] = 2.0
    engine._l5_cost_units_by_workspace["ws"] = 3.0
    engine._l5_admitted_timestamps_by_lang["python"] = [1.0]
    engine._l5_cooldown_until_by_scope_file["k"] = 5.0

    registry = EnrichRuntimeServiceRegistry(engine)
    reset = registry.l5_runtime_stats_service().reset_runtime_state(
        reject_counts=engine._l5_reject_counts_by_reason,
        cost_units_by_reason=engine._l5_cost_units_by_reason,
        cost_units_by_language=engine._l5_cost_units_by_language,
        cost_units_by_workspace=engine._l5_cost_units_by_workspace,
        admitted_timestamps_by_lang=engine._l5_admitted_timestamps_by_lang,
        cooldown_until_by_scope_file=engine._l5_cooldown_until_by_scope_file,
    )

    assert reset["l5_total_decisions"] == 0
    assert engine._l5_cost_units_by_reason == {}
    assert engine._l5_cost_units_by_language == {}
    assert engine._l5_cost_units_by_workspace == {}
