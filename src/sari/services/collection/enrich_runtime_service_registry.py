"""EnrichEngine의 런타임 lazy-init 서비스 레지스트리."""

from __future__ import annotations

import queue
import time
from typing import TYPE_CHECKING

from sari.core.language.registry import resolve_language_from_path
from sari.core.models import now_iso8601_utc
from sari.services.collection.l3.l3_bootstrap_mode_service import L3BootstrapModeService
from sari.services.collection.l3.l3_error_handling_service import L3ErrorHandlingService
from sari.services.collection.l3.l3_runtime_coordination_service import L3RuntimeCoordinationService
from sari.services.collection.l3.l3_scheduling_service import L3SchedulingService
from sari.services.collection.l3.l3_skip_runtime_service import L3SkipRuntimeService
from sari.services.collection.l5.l5_runtime_stats_service import L5RuntimeStatsService

if TYPE_CHECKING:
    from sari.services.collection.enrich_engine import EnrichEngine


class EnrichRuntimeServiceRegistry:
    """EnrichEngine 내부 lazy-init 서비스 생성/캐시를 전담한다."""

    def __init__(self, engine: "EnrichEngine") -> None:
        self._engine = engine

    def l3_scheduling_service(self) -> L3SchedulingService:
        engine = self._engine
        existing = getattr(engine, "_l3_scheduling_service", None)
        if existing is not None:
            return existing
        created = L3SchedulingService(
            resolve_lsp_language=lambda relative_path: engine._resolve_lsp_language(relative_path),
            lsp_backend=getattr(engine, "_lsp_backend", object()),
            l3_parallel_enabled=bool(getattr(engine, "_l3_parallel_enabled", True)),
            executor_max_workers=max(1, int(getattr(engine, "_l3_executor_max_workers", 32))),
            backpressure_on_interactive=bool(getattr(engine, "_l3_backpressure_on_interactive", True)),
            backpressure_cooldown_sec=float(getattr(engine, "_l3_backpressure_cooldown_sec", 0.3)),
            monotonic_now=time.monotonic,
        )
        engine._l3_scheduling_service = created
        return created

    def l3_error_handling_service(self) -> L3ErrorHandlingService:
        engine = self._engine
        existing = getattr(engine, "_l3_error_handling_service", None)
        if existing is not None:
            return existing
        created = L3ErrorHandlingService(
            queue_repo=getattr(engine, "_enrich_queue_repo", object()),
            error_policy=getattr(engine, "_error_policy", object()),
            now_iso_supplier=now_iso8601_utc,
            min_defer_sec=max(0, int(getattr(engine, "_l5_min_defer_sec", 5))),
        )
        engine._l3_error_handling_service = created
        return created

    def l3_skip_runtime_service(self) -> L3SkipRuntimeService:
        engine = self._engine
        existing = getattr(engine, "_l3_skip_runtime_service", None)
        if existing is not None:
            return existing
        created = L3SkipRuntimeService(
            l3_supported_languages=getattr(engine, "_l3_supported_languages", set()),
            l3_recent_success_ttl_sec=int(getattr(engine, "_l3_recent_success_ttl_sec", 0)),
            readiness_repo=getattr(engine, "_readiness_repo", object()),
            lsp_backend=getattr(engine, "_lsp_backend", object()),
            resolve_language_from_path_fn=lambda relative_path: resolve_language_from_path(file_path=relative_path),
        )
        engine._l3_skip_runtime_service = created
        return created

    def l3_runtime_coordination_service(self) -> L3RuntimeCoordinationService:
        engine = self._engine
        existing = getattr(engine, "_l3_runtime_coordination_service", None)
        if existing is not None:
            return existing
        created = L3RuntimeCoordinationService(
            lsp_backend=getattr(engine, "_lsp_backend", object()),
            lsp_probe_l1_languages=getattr(engine, "_lsp_probe_l1_languages", set()),
            resolve_language_from_path_fn=lambda relative_path: resolve_language_from_path(file_path=relative_path),
            l3_ready_queue=getattr(engine, "_l3_ready_queue", queue.Queue()),
            enrich_queue_repo=getattr(engine, "_enrich_queue_repo", object()),
            now_iso_supplier=now_iso8601_utc,
            policy_repo=getattr(engine, "_policy_repo", None),
        )
        engine._l3_runtime_coordination_service = created
        return created

    def l3_bootstrap_mode_service(self) -> L3BootstrapModeService:
        engine = self._engine
        existing = getattr(engine, "_l3_bootstrap_mode_service", None)
        if isinstance(existing, L3BootstrapModeService):
            return existing
        created = L3BootstrapModeService(
            file_repo=getattr(engine, "_file_repo", object()),
            policy_repo=getattr(engine, "_policy_repo", None),
        )
        engine._l3_bootstrap_mode_service = created
        return created

    def l5_runtime_stats_service(self) -> L5RuntimeStatsService:
        engine = self._engine
        existing = getattr(engine, "_l5_runtime_stats_service", None)
        if isinstance(existing, L5RuntimeStatsService):
            return existing
        created = L5RuntimeStatsService()
        engine._l5_runtime_stats_service = created
        return created
