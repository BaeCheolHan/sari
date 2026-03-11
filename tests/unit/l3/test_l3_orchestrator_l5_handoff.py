from __future__ import annotations

from dataclasses import replace

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
import pytest


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
    def __init__(self) -> None:
        self.zero_relation_calls: list[tuple[str, int]] = []

    def defer_after_l5_admission_rejection(self, *, job: FileEnrichJobDTO, admission) -> bool:  # noqa: ANN001
        _ = (job, admission)
        return False

    def defer_after_preprocess_heavy(self, *, job: FileEnrichJobDTO, reason: str) -> bool:
        _ = (job, reason)
        return False

    def defer_after_zero_relations(self, *, job: FileEnrichJobDTO) -> bool:
        self.zero_relation_calls.append((job.job_id, job.deferred_count))
        return job.deferred_count < 1


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


def _build_orchestrator(*, extract_fn, raw_extract_fn=None, is_recent_l5_ready=None):
    l5_queue = _NoopL5QueueTransition()
    skip = L3SkipEligibilityService(
        is_recent_tool_ready=lambda _job: False,
        resolve_l3_skip_reason=lambda _job: None,
        build_l3_skipped_readiness=lambda _job, _reason, _now_iso: None,  # type: ignore[return-value]
        is_recent_l5_ready=is_recent_l5_ready,
    )
    orchestrator = L3Orchestrator(
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
        l5_queue_transition=l5_queue,
        skip_eligibility=skip,
        persist_service=L3PersistService(record_scope_learning=lambda _job: None),
        preprocess_service=_NeedsL5Preprocess(),
        degraded_fallback_service=None,
        preprocess_max_bytes=1024,
        extract_fn=extract_fn,
        raw_extract_fn=raw_extract_fn,
    )
    return orchestrator, l5_queue


def test_l3_process_l5_job_executes_extract_and_persists_l5_layers() -> None:
    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(
            symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
            relations=[{"from_symbol": "A", "to_symbol": "B", "line": 1}],
            error_message=None,
        )

    orchestrator, _l5_queue = _build_orchestrator(extract_fn=_extract, is_recent_l5_ready=lambda _job: False)

    result = orchestrator.process_l5_job(_job())

    assert result["finished_status"] == "DONE"
    assert result["done_id"] == "j1"
    assert result["layer_upserts"].l5_layer_upsert is not None


def test_l3_process_l5_job_skips_when_l5_semantics_exist() -> None:
    """process_l5_job에서 l5_semantics가 이미 있으면 extract 없이 DONE."""
    extract_called = []

    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        extract_called.append((repo_root, relative_path, content_hash))
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

    orchestrator, _l5_queue = _build_orchestrator(extract_fn=_extract, is_recent_l5_ready=lambda _job: True)

    result = orchestrator.process_l5_job(_job())

    assert result["finished_status"] == "DONE"
    assert len(extract_called) == 0  # extract는 호출되지 않아야 함

def test_l3_process_l5_job_keeps_zero_relations_as_normal_l5_success() -> None:
    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(
            symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
            relations=[],
            error_message=None,
        )

    orchestrator, l5_queue = _build_orchestrator(extract_fn=_extract, is_recent_l5_ready=lambda _job: False)

    result = orchestrator.process_l5_job(_job())

    assert result["finished_status"] == "DONE"
    assert result["done_id"] == "j1"
    assert result["readiness_update"].last_reason == "ok"
    assert result["readiness_update"].get_callers_ready is False
    assert l5_queue.zero_relation_calls == []


def test_l3_process_l5_job_defers_suspicious_zero_relations_once() -> None:
    def _extract(repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(
            symbols=[{"name": f"f{i}", "kind": "function", "line": i + 1, "end_line": i + 1} for i in range(25)],
            relations=[],
            error_message=None,
        )

    orchestrator, l5_queue = _build_orchestrator(extract_fn=_extract, is_recent_l5_ready=lambda _job: False)

    result = orchestrator.process_l5_job(_job())

    assert result["finished_status"] == "DONE"
    assert result["done_id"] == "j1"
    assert result["readiness_update"].last_reason == "ok_zero_relations_retry_pending"
    assert l5_queue.zero_relation_calls == [("j1", 0)]



def test_l3_process_l5_retry_zero_relations_bypasses_cached_extract() -> None:
    cached_calls: list[tuple[str, str, str, bool]] = []

    def _cached(
        repo_root: str,
        relative_path: str,
        content_hash: str,
        *,
        bypass_zero_relations_retry_pending: bool = False,
    ) -> LspExtractionResultDTO:
        cached_calls.append((repo_root, relative_path, content_hash, bypass_zero_relations_retry_pending))
        if bypass_zero_relations_retry_pending:
            return LspExtractionResultDTO(
                symbols=[{"name": "raw", "kind": "function", "line": 1, "end_line": 1}],
                relations=[{"from_symbol": "A", "to_symbol": "B", "line": 1}],
                error_message=None,
            )
        return LspExtractionResultDTO(
            symbols=[{"name": "cached", "kind": "function", "line": 1, "end_line": 1}],
            relations=[],
            error_message=None,
        )

    orchestrator, _l5_queue = _build_orchestrator(extract_fn=_cached, is_recent_l5_ready=lambda _job: False)
    retry_job = replace(_job(), defer_reason="retry_zero_relations", deferred_count=1, enqueue_source="l5")

    result = orchestrator.process_l5_job(retry_job)

    assert result["finished_status"] == "DONE"
    assert cached_calls == [("/repo", "a.py", "h1", True)]
    assert result["readiness_update"].get_callers_ready is True


def test_l3_process_l5_retry_zero_relations_does_not_mask_internal_type_error() -> None:
    def _cached(
        repo_root: str,
        relative_path: str,
        content_hash: str,
        *,
        bypass_zero_relations_retry_pending: bool = False,
    ) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash, bypass_zero_relations_retry_pending)
        raise TypeError("real extractor type error")

    orchestrator, _l5_queue = _build_orchestrator(extract_fn=_cached, is_recent_l5_ready=lambda _job: False)
    retry_job = replace(_job(), defer_reason="retry_zero_relations", deferred_count=1, enqueue_source="l5")

    with pytest.raises(TypeError, match="real extractor type error"):
        orchestrator.process_l5_job(retry_job)
