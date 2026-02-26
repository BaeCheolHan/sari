"""Batch17 계열 테스트에서 사용하는 최소 EnrichEngine 생성기."""

from __future__ import annotations

from solidlsp.ls_config import Language

from sari.core.models import FileEnrichJobDTO, now_iso8601_utc
from sari.services.collection.enrich_engine import EnrichEngine
from sari.services.collection.enrich_result_dto import _L3JobResultDTO
from sari.services.collection.l3.l3_failure_classifier import classify_l3_extract_failure_kind
from sari.services.collection.l3.l3_orchestrator import L3Orchestrator
from sari.services.collection.perf_trace import PerfTracer


class _StubFileRow:
    def __init__(self, *, content_hash: str) -> None:
        self.is_deleted = False
        self.content_hash = content_hash


class _StubFileRepo:
    def __init__(self, *, content_hash: str) -> None:
        self._row = _StubFileRow(content_hash=content_hash)

    def get_file(self, repo_root: str, relative_path: str):  # noqa: ANN001
        _ = (repo_root, relative_path)
        return self._row


class _StubReadinessRepo:
    def get_state(self, repo_root: str, relative_path: str):  # noqa: ANN001
        _ = (repo_root, relative_path)
        return None


def build_min_enrich_engine_for_l3_test(*, lsp_backend: object, queue_repo: object, error_policy: object) -> EnrichEngine:
    """L3 흐름 단위 테스트용 최소 EnrichEngine 인스턴스를 생성한다."""
    engine = object.__new__(EnrichEngine)
    engine._perf_tracer = PerfTracer(component="test_enrich_engine")
    engine._file_repo = _StubFileRepo(content_hash="h1")
    engine._enrich_queue_repo = queue_repo
    engine._readiness_repo = _StubReadinessRepo()
    engine._lsp_backend = lsp_backend
    engine._policy = type("P", (), {"retry_max_attempts": 5, "retry_backoff_base_sec": 1})()
    engine._run_mode = "prod"
    engine._policy_repo = None
    engine._event_repo = None
    engine._error_policy = error_policy
    engine._record_enrich_latency = lambda ms: None
    engine._l3_recent_success_ttl_sec = 0
    engine._lsp_probe_l1_languages = set()
    engine._l3_supported_languages = {Language.PYTHON}
    engine._schedule_l1_probe_after_l3_fallback_called = 0
    engine._l5_total_decisions = 0
    engine._l5_total_admitted = 0
    engine._l5_batch_decisions = 0
    engine._l5_batch_admitted = 0
    engine._l5_admission_shadow_enabled = False
    engine._l5_admission_enforced = False
    engine._l5_calls_per_min_per_lang_max = 30
    engine._l5_admitted_timestamps_by_lang = {}

    class _StubQueueTransitionService:
        def defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
            if "ERR_LSP_BROKER_LEASE_REQUIRED" not in str(error_message):
                return False
            if hasattr(queue_repo, "defer_jobs_to_pending"):
                changed = queue_repo.defer_jobs_to_pending(
                    job_ids=[job.job_id],
                    next_retry_at="2026-01-01T00:00:20+00:00",
                    defer_reason="l5_defer:pressure_burst_exceeded",
                    now_iso="2026-01-01T00:00:00+00:00",
                )
                return int(changed) > 0
            return False

        def escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
            message = str(error_message).lower()
            trigger = ("no workspace contains" in message) or ("workspace_mismatch" in message)
            if not trigger:
                return False
            if int(getattr(job, "scope_attempts", 0)) >= 2:
                return False
            if hasattr(queue_repo, "escalate_scope_on_same_job"):
                return bool(
                    queue_repo.escalate_scope_on_same_job(
                        job_id=job.job_id,
                        next_scope_level="repo",
                        next_scope_root=job.repo_root,
                        next_retry_at="2026-01-01T00:00:00+00:00",
                        now_iso="2026-01-01T00:00:00+00:00",
                    )
                )
            return False

    class _StubL5QueueDeferService:
        def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission) -> bool:  # noqa: ANN001
            _ = admission
            if hasattr(queue_repo, "defer_jobs_to_pending"):
                changed = queue_repo.defer_jobs_to_pending(
                    job_ids=[job.job_id],
                    next_retry_at="2026-01-01T00:00:30+00:00",
                    defer_reason="l5_defer:pressure_rate_exceeded",
                    now_iso="2026-01-01T00:00:00+00:00",
                )
                return int(changed) > 0
            return False

        def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
            _ = reason
            if hasattr(queue_repo, "defer_jobs_to_pending"):
                changed = queue_repo.defer_jobs_to_pending(
                    job_ids=[job.job_id],
                    next_retry_at="2026-01-01T00:01:00+00:00",
                    defer_reason="l5_defer:deferred_heavy:test",
                    now_iso="2026-01-01T00:00:00+00:00",
                )
                return int(changed) > 0
            return False

    engine._l3_queue_transition_service = _StubQueueTransitionService()
    engine._l5_queue_defer_service = _StubL5QueueDeferService()

    def _fallback_probe(*, job):  # noqa: ANN002, ANN003
        _ = job
        engine._schedule_l1_probe_after_l3_fallback_called += 1

    engine._schedule_l1_probe_after_l3_fallback = _fallback_probe

    class _SkipEligibilityAdapter:
        def is_recent_tool_ready(self, job: FileEnrichJobDTO) -> bool:
            return bool(engine._is_recent_tool_ready(job=job))

        def resolve_skip_reason(self, job: FileEnrichJobDTO) -> str | None:
            return engine._resolve_l3_skip_reason(job=job)

        def build_skipped_readiness(self, *, job: FileEnrichJobDTO, reason: str, now_iso: str):
            return engine._build_l3_skipped_readiness(job=job, reason=reason, now_iso=now_iso)

    class _ScopeResolutionAdapter:
        def resolve_language(self, relative_path: str):
            return engine._resolve_lsp_language(relative_path)

    class _PersistServiceStub:
        pass

    class _DelegatingOrchestrator:
        def process_job(self, job: FileEnrichJobDTO):  # noqa: ANN001
            orchestrator = L3Orchestrator(
                file_repo=engine._file_repo,
                lsp_backend=engine._lsp_backend,
                policy=engine._policy,
                error_policy=engine._error_policy,
                run_mode=engine._run_mode,
                event_repo=engine._event_repo,
                deletion_hold_enabled=lambda: bool(getattr(engine, "_deletion_hold_enabled", False)),
                now_iso_supplier=now_iso8601_utc,
                record_enrich_latency=engine._record_enrich_latency,
                result_builder=lambda **kwargs: _L3JobResultDTO(**kwargs),
                classify_failure_kind=classify_l3_extract_failure_kind,
                schedule_l1_probe_after_l3_fallback=lambda j: engine._schedule_l1_probe_after_l3_fallback(job=j),
                scope_resolution=_ScopeResolutionAdapter(),
                queue_transition=engine._l3_queue_transition_service,
                l5_queue_transition=engine._l5_queue_defer_service,
                skip_eligibility=_SkipEligibilityAdapter(),
                persist_service=_PersistServiceStub(),
                preprocess_service=getattr(engine, "_l3_preprocess_service", None),
                degraded_fallback_service=getattr(engine, "_l3_degraded_fallback_service", None),
                preprocess_max_bytes=int(getattr(engine, "_l3_preprocess_max_bytes", 262_144)),
                evaluate_l5_admission=(
                    (lambda job_arg, language_key: engine._evaluate_l5_admission_for_job(job_arg, language_key))
                    if bool(getattr(engine, "_l5_admission_shadow_enabled", False))
                    else None
                ),
                l5_admission_enforced=bool(getattr(engine, "_l5_admission_enforced", False)),
            )
            return orchestrator.process_job(job)

    engine._l3_orchestrator = _DelegatingOrchestrator()
    return engine
