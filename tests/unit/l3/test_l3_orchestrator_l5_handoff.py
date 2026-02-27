from __future__ import annotations

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_orchestrator import L3Orchestrator
from sari.services.collection.l3.l3_persist_service import L3PersistService
from sari.services.collection.l3.l3_scope_resolution_service import L3ScopeResolutionService
from sari.services.collection.l3.l3_skip_eligibility_service import L3SkipEligibilityService
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)
from sari.services.lsp_extraction_contracts import LspExtractionResultDTO


class _StubFileRow:
    is_deleted = False
    content_hash = "h1"


class _StubFileRepo:
    def get_file(self, repo_root: str, relative_path: str):  # noqa: ANN001
        _ = (repo_root, relative_path)
        return _StubFileRow()


class _NoopErrorPolicy:
    def record_error_event(self, **kwargs: object) -> None:
        _ = kwargs


class _NoopQueueTransition:
    def defer_after_broker_lease_denial(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return False

    def escalate_scope_after_l3_extract_error(self, *, job: FileEnrichJobDTO, error_message: str) -> bool:
        _ = (job, error_message)
        return False


class _NoopL5QueueTransition:
    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission) -> bool:  # noqa: ANN001
        _ = (job, admission)
        return False

    def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
        _ = (job, reason)
        return False


class _NeedsL5Preprocess:
    def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int = 0) -> L3PreprocessResultDTO:
        _ = (relative_path, content_text, max_bytes)
        return L3PreprocessResultDTO(
            symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
            degraded=False,
            decision=L3PreprocessDecision.NEEDS_L5,
            source="tree_sitter",
            reason="needs_l5",
        )


def _job() -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-03-01T00:00:00+00:00",
        created_at="2026-03-01T00:00:00+00:00",
        updated_at="2026-03-01T00:00:00+00:00",
    )


def _build_orchestrator(*, extract_fn, handoff_to_l5):
    skip = L3SkipEligibilityService(
        is_recent_tool_ready=lambda _job: False,
        resolve_l3_skip_reason=lambda _job: None,
        build_l3_skipped_readiness=lambda _job, _reason, _now_iso: None,  # type: ignore[return-value]
    )
    return L3Orchestrator(
        file_repo=_StubFileRepo(),
        lsp_backend=object(),
        policy=type("P", (), {"retry_max_attempts": 3, "retry_backoff_base_sec": 1})(),
        error_policy=_NoopErrorPolicy(),
        run_mode="prod",
        event_repo=None,
        deletion_hold_enabled=lambda: False,
        now_iso_supplier=lambda: "2026-03-01T00:00:00+00:00",
        record_enrich_latency=lambda _ms: None,
        result_builder=lambda **kwargs: kwargs,
        classify_failure_kind=lambda _msg: "TRANSIENT",
        schedule_l1_probe_after_l3_fallback=lambda _job: None,
        scope_resolution=L3ScopeResolutionService(),
        queue_transition=_NoopQueueTransition(),
        l5_queue_transition=_NoopL5QueueTransition(),
        skip_eligibility=skip,
        persist_service=L3PersistService(record_scope_learning=lambda _job: None),
        preprocess_service=_NeedsL5Preprocess(),
        degraded_fallback_service=None,
        preprocess_max_bytes=1024,
        extract_fn=extract_fn,
        handoff_to_l5=handoff_to_l5,
    )


def test_l3_process_job_handoffs_needs_l5_without_extract() -> None:
    handoff_calls: list[str] = []

    def _extract_should_not_run(repo_root: str, relative_path: str, content_hash: str):
        _ = (repo_root, relative_path, content_hash)
        raise AssertionError("extract should not run when handoff succeeds")

    orchestrator = _build_orchestrator(
        extract_fn=_extract_should_not_run,
        handoff_to_l5=lambda job, now_iso: handoff_calls.append(f"{job.job_id}:{now_iso}") or True,
    )

    result = orchestrator.process_job(_job())

    assert result["finished_status"] == "PENDING"
    assert result["done_id"] is None
    assert len(handoff_calls) == 1


def test_l3_process_job_marks_failed_when_handoff_fails() -> None:
    """handoff가 False면 RUNNING 고착을 피하기 위해 FAILED 경로로 되돌려야 한다."""
    extract_calls: list[str] = []

    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        extract_calls.append(f"{repo_root}:{relative_path}:{content_hash}")
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

    orchestrator = _build_orchestrator(
        extract_fn=_extract,
        handoff_to_l5=lambda _job, _now_iso: False,
    )

    result = orchestrator.process_job(_job())

    assert result["finished_status"] == "FAILED"
    assert result["failure_update"] is not None
    assert result["state_update"] is not None
    assert len(extract_calls) == 0


def test_l3_process_l5_job_executes_extract_and_persists_l5_layers() -> None:
    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(
            symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
            relations=[],
            error_message=None,
        )

    orchestrator = _build_orchestrator(
        extract_fn=_extract,
        handoff_to_l5=lambda _job, _now_iso: True,
    )

    result = orchestrator.process_l5_job(_job())

    assert result["finished_status"] == "DONE"
    assert result["done_id"] == "j1"
    assert result["layer_upserts"].l5_layer_upsert is not None
