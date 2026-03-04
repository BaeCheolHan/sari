"""Batch-17 성능/자원관리 하드닝 요구사항을 검증한다."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
import sqlite3
import time
import math

import hashlib
import pytest

from sari.core.models import CandidateIndexChangeDTO, CollectedFileL1DTO, CollectionPolicyDTO, FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason, ToolReadinessStateDTO, WorkspaceDTO, now_iso8601_utc
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from solidlsp.ls_config import Language
from sari.search.candidate_search import CandidateBackendError, CandidateSearchConfig, CandidateSearchResultDTO, TantivyCandidateBackend
from sari.search.orchestrator import SearchOrchestrator
from sari.services.collection.enrich_engine import EnrichEngine
from sari.services.collection.enrich_result_dto import _L3JobResultDTO
from sari.services.collection.l3.l3_failure_classifier import classify_l3_extract_failure_kind
from sari.services.collection.l3.l3_orchestrator import L3Orchestrator
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
    L3TreeSitterPreprocessService,
)
from sari.services.collection.l5.lsp.session_broker import LspBrokerLanguageProfile, LspSessionBroker
from sari.services.collection.perf_trace import PerfTracer
from sari.services.collection.testing.enrich_engine_test_factory import (
    build_min_enrich_engine_for_l3_test,
)
from sari.services.collection.l1.watcher_hotness_tracker import WatcherHotnessTracker
from sari.services.collection.enrich_flush_coordinator import EnrichFlushCoordinator
from sari.services.collection.l3.l3_scheduling_service import L3SchedulingService
from sari.services.collection.service import FileCollectionService, SolidLspExtractionBackend
from sari.services.lsp_extraction_contracts import LspExtractionBackend, LspExtractionResultDTO
import sari.services.collection.service as file_collection_service_module
import sari.db.repositories.file_collection_repository as file_collection_repository_module


class _NoopLspBackend(LspExtractionBackend):
    """테스트용 no-op LSP 추출 백엔드다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """항상 빈 LSP 결과를 반환한다."""
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


class _ProbeCountingBackend(_NoopLspBackend):
    """probe 스케줄 호출 여부를 검증하기 위한 테스트 더블."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str]] = []

    def schedule_probe_for_file(
        self,
        repo_root: str,
        relative_path: str,
        force: bool = False,
        trigger: str = "background",
    ) -> str:
        self.calls.append((repo_root, relative_path, bool(force), str(trigger)))
        return "scheduled"


class _StubErrorPolicy:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record_error_event(self, **kwargs) -> None:  # noqa: ANN003
        self.events.append((str(kwargs.get("error_code")), str(kwargs.get("phase"))))


class _CaptureEscalateQueueRepo:
    def __init__(self, *, escalate_returns: bool = True) -> None:
        self.calls: list[dict[str, str]] = []
        self.escalate_returns = escalate_returns
        self.defer_calls: list[dict[str, object]] = []

    def escalate_scope_on_same_job(
        self,
        *,
        job_id: str,
        next_scope_level: str,
        next_scope_root: str,
        next_retry_at: str,
        now_iso: str,
    ) -> bool:
        self.calls.append(
            {
                "job_id": job_id,
                "next_scope_level": next_scope_level,
                "next_scope_root": next_scope_root,
                "next_retry_at": next_retry_at,
                "now_iso": now_iso,
            }
        )
        return self.escalate_returns

    def defer_jobs_to_pending(self, *, job_ids: list[str], next_retry_at: str, defer_reason: str, now_iso: str) -> int:
        self.defer_calls.append(
            {
                "job_ids": list(job_ids),
                "next_retry_at": next_retry_at,
                "defer_reason": defer_reason,
                "now_iso": now_iso,
            }
        )
        return len(job_ids)


class _StubExtractBackend:
    def __init__(self, error_message: str | None) -> None:
        self.error_message = error_message

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=self.error_message)


class _StubExtractBackendShouldNotBeCalled:
    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        raise AssertionError("LSP extract should not be called when preprocess skips")

class _CaptureToolLayerRepo:
    def __init__(self) -> None:
        self.l3_calls: list[dict[str, object]] = []
        self.l4_calls: list[dict[str, object]] = []
        self.l5_calls: list[dict[str, object]] = []
        self.l3_many_calls: list[list[dict[str, object]]] = []
        self.l4_many_calls: list[list[dict[str, object]]] = []
        self.l5_many_calls: list[list[dict[str, object]]] = []

    def upsert_l3_symbols(self, **kwargs) -> None:  # noqa: ANN003
        self.l3_calls.append(dict(kwargs))

    def upsert_l4_normalized_symbols(self, **kwargs) -> None:  # noqa: ANN003
        self.l4_calls.append(dict(kwargs))

    def upsert_l5_semantics(self, **kwargs) -> None:  # noqa: ANN003
        self.l5_calls.append(dict(kwargs))

    def upsert_l3_symbols_many(self, upserts: list[dict[str, object]]) -> None:
        self.l3_many_calls.append([dict(item) for item in upserts])

    def upsert_l4_normalized_symbols_many(self, upserts: list[dict[str, object]]) -> None:
        self.l4_many_calls.append([dict(item) for item in upserts])

    def upsert_l5_semantics_many(self, upserts: list[dict[str, object]]) -> None:
        self.l5_many_calls.append([dict(item) for item in upserts])



def test_enrich_engine_l3_refactored_orchestrator_flag_routes_single_job() -> None:
    """리팩터링 플래그 ON이면 단일 L3 job 처리는 오케스트레이터로 위임되어야 한다."""

    class _CaptureOrchestrator:
        def __init__(self) -> None:
            self.calls: list[FileEnrichJobDTO] = []

        def process_job(self, job: FileEnrichJobDTO):  # noqa: ANN001
            self.calls.append(job)
            return _L3JobResultDTO(
                job_id=job.job_id,
                finished_status="DONE",
                elapsed_ms=1.0,
                done_id=job.job_id,
                failure_update=None,
                state_update=None,
                body_delete=None,
                lsp_update=None,
                readiness_update=None,
                dev_error=None,
            )

    engine = object.__new__(EnrichEngine)
    engine._l3_orchestrator = _CaptureOrchestrator()
    engine._record_enrich_latency = lambda _ms: None
    engine._event_repo = None
    engine._perf_tracer = PerfTracer(component="test_enrich_engine")

    job = FileEnrichJobDTO(
        job_id="j-refactor-route",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=1,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    result = engine._process_single_l3_job(job)

    assert result.finished_status == "DONE"
    assert result.done_id == "j-refactor-route"
    assert len(engine._l3_orchestrator.calls) == 1
    assert engine._l3_orchestrator.calls[0].job_id == "j-refactor-route"


def test_l3_orchestrator_quality_shadow_records_sampled_result() -> None:
    orchestrator = object.__new__(L3Orchestrator)
    orchestrator._quality_shadow_enabled = True
    orchestrator._quality_shadow_sample_rate = 1.0
    orchestrator._quality_shadow_max_files = 10
    orchestrator._quality_shadow_sampled_count = 0
    orchestrator._quality_shadow_lang_allowlist = {"java"}
    orchestrator._quality_shadow_accumulators = {}
    orchestrator._quality_shadow_flag_counts = {}
    orchestrator._quality_shadow_missing_pattern_counts = {}
    orchestrator._quality_shadow_eval_errors = 0

    class _EvalService:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, **kwargs):  # noqa: ANN003
            self.calls += 1
            return type(
                "_Result",
                (),
                {
                    "symbol_recall_proxy": 0.5,
                    "symbol_precision_proxy": 1.0,
                    "kind_match_rate": 0.75,
                    "position_match_rate": 1.0,
                    "ast_symbol_count": 1,
                    "lsp_symbol_count": 2,
                    "quality_flags": ("ast_missing_symbols",),
                    "missing_patterns": ("missing_field", "missing_constructor"),
                },
            )()

    eval_service = _EvalService()
    orchestrator._quality_eval_service = eval_service

    job = FileEnrichJobDTO(
        job_id="q1",
        repo_id="r1",
        repo_root="/repo",
        relative_path="src/A.java",
        content_hash="h1",
        priority=1,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    preprocess_result = L3PreprocessResultDTO(
        symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 10}],
        degraded=False,
        decision=L3PreprocessDecision.L3_ONLY,
        source="tree_sitter",
        reason="ok",
    )

    orchestrator._record_quality_shadow_compare(
        job=job,
        language="java",
        preprocess_result=preprocess_result,
        lsp_symbols=[
            {"name": "A", "kind": "class", "line": 1, "end_line": 10},
            {"name": "m", "kind": "method", "line": 3, "end_line": 3},
        ],
    )

    summary = orchestrator.get_quality_shadow_summary()
    assert eval_service.calls == 1
    assert summary["enabled"] is True
    assert summary["sampled_files"] == 1
    assert summary["sampled_files_by_language"]["java"] == 1
    assert summary["avg_recall_proxy_by_language"]["java"] == pytest.approx(0.5)
    assert summary["quality_flags_top_counts"]["ast_missing_symbols"] == 1
    top_missing = summary["missing_patterns_top_by_language"]["java"]
    assert top_missing[0]["pattern"] == "missing_constructor"
    assert top_missing[0]["count"] == 1
    assert top_missing[1]["pattern"] == "missing_field"
    assert top_missing[1]["count"] == 1


def test_l3_orchestrator_quality_shadow_swallows_eval_errors() -> None:
    orchestrator = object.__new__(L3Orchestrator)
    orchestrator._quality_shadow_enabled = True
    orchestrator._quality_shadow_sample_rate = 1.0
    orchestrator._quality_shadow_max_files = 10
    orchestrator._quality_shadow_sampled_count = 0
    orchestrator._quality_shadow_lang_allowlist = {"java"}
    orchestrator._quality_shadow_accumulators = {}
    orchestrator._quality_shadow_flag_counts = {}
    orchestrator._quality_shadow_missing_pattern_counts = {}
    orchestrator._quality_shadow_eval_errors = 0

    class _EvalService:
        def evaluate(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("boom")

    orchestrator._quality_eval_service = _EvalService()
    job = FileEnrichJobDTO(
        job_id="q2",
        repo_id="r1",
        repo_root="/repo",
        relative_path="src/B.java",
        content_hash="h2",
        priority=1,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    preprocess_result = L3PreprocessResultDTO(
        symbols=[{"name": "B", "kind": "class", "line": 1, "end_line": 10}],
        degraded=False,
        decision=L3PreprocessDecision.L3_ONLY,
        source="tree_sitter",
        reason="ok",
    )

    orchestrator._record_quality_shadow_compare(
        job=job,
        language="java",
        preprocess_result=preprocess_result,
        lsp_symbols=[{"name": "B", "kind": "class", "line": 1, "end_line": 10}],
    )

    summary = orchestrator.get_quality_shadow_summary()
    assert summary["shadow_eval_errors"] == 1
    assert summary["sampled_files"] == 0


def test_enrich_engine_evaluate_l5_admission_applies_workspace_content_hash_cooldown() -> None:
    """동일 workspace+content_hash 요청은 cooldown 기간에 COOLDOWN_ACTIVE로 거부되어야 한다."""

    class _StubAdmissionService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def evaluate_batch(self, **kwargs):  # noqa: ANN003
            self.calls.append(dict(kwargs))
            cooldown_active = bool(kwargs.get("cooldown_active", False))
            if cooldown_active:
                return L4AdmissionDecisionDTO(
                    admit_l5=False,
                    reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                    reject_reason=L5RejectReason.COOLDOWN_ACTIVE,
                )
            return L4AdmissionDecisionDTO(
                admit_l5=False,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
            )

    engine = object.__new__(EnrichEngine)
    engine._l4_admission_service = _StubAdmissionService()
    engine._l5_total_decisions = 0
    engine._l5_total_admitted = 0
    engine._l5_batch_decisions = 0
    engine._l5_batch_admitted = 0
    engine._l5_calls_per_min_per_lang_max = 999
    engine._l5_admitted_timestamps_by_lang = {}
    engine._l5_reject_counts_by_reason = {reason: 0 for reason in L5RejectReason}
    engine._l5_cooldown_until_by_scope_file = {}
    engine._schedule_l4_admission_probe = lambda job: None

    job = FileEnrichJobDTO(
        job_id="j-cooldown",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo/src/a.py",
        content_hash="h-cool",
        priority=1,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    first = engine._evaluate_l5_admission_for_job(job, "python")
    second = engine._evaluate_l5_admission_for_job(job, "python")

    assert first is not None
    assert first.reject_reason == L5RejectReason.PRESSURE_RATE_EXCEEDED
    assert second is not None
    assert second.reject_reason == L5RejectReason.COOLDOWN_ACTIVE
    assert len(engine._l4_admission_service.calls) == 2
    assert bool(engine._l4_admission_service.calls[1].get("cooldown_active", False)) is True


def test_enrich_engine_flush_persists_tool_layer_buffers() -> None:
    """flush 단계에서 L3/L4/L5 tool_data 버퍼가 저장소로 반영되어야 한다."""
    body_repo = type("BodyRepo", (), {"upsert_body_many": lambda self, _: None, "delete_body_many": lambda self, _: None})()
    lsp_repo = type("LspRepo", (), {"replace_file_data_many": lambda self, _: None})()
    readiness_repo = type("ReadinessRepo", (), {"upsert_state_many": lambda self, _: None})()
    file_repo = type("FileRepo", (), {"update_enrich_state_many": lambda self, _: None})()
    enrich_queue_repo = type(
        "QueueRepo",
        (),
        {"mark_done_many": lambda self, _: None, "mark_failed_with_backoff_many": lambda self, _: None},
    )()
    tool_layer_repo = _CaptureToolLayerRepo()
    coordinator = EnrichFlushCoordinator(
        body_repo=body_repo,
        lsp_repo=lsp_repo,
        readiness_repo=readiness_repo,
        file_repo=file_repo,
        enrich_queue_repo=enrich_queue_repo,
        tool_layer_repo=tool_layer_repo,
    )

    from sari.services.collection.enrich_result_dto import _L3ResultBuffersDTO as _L3Buf, _LayerUpsertBucketsDTO

    l3_upsert = {
        "workspace_id": "ws",
        "repo_root": "/workspace",
        "relative_path": "a.py",
        "content_hash": "h1",
        "symbols": [{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
        "degraded": False,
        "l3_skipped_large_file": False,
        "updated_at": "2026-02-23T00:00:00+00:00",
    }
    l4_upsert = {
        "workspace_id": "ws",
        "repo_root": "/workspace",
        "relative_path": "a.py",
        "content_hash": "h1",
        "normalized": {"decision": "l3_only"},
        "confidence": 0.9,
        "ambiguity": 0.1,
        "coverage": 1.0,
        "updated_at": "2026-02-23T00:00:00+00:00",
    }
    l5_upsert = {
        "workspace_id": "ws",
        "repo_root": "/workspace",
        "relative_path": "a.py",
        "content_hash": "h1",
        "reason_code": "L5_REASON_GOLDENSET_COVERAGE",
        "semantics": {"relations_count": 1},
        "updated_at": "2026-02-23T00:00:00+00:00",
    }
    buffers = _L3Buf(
        layer_upsert_buckets=_LayerUpsertBucketsDTO(
            l3_layer_upserts=[l3_upsert],
            l4_layer_upserts=[l4_upsert],
            l5_layer_upserts=[l5_upsert],
        )
    )
    coordinator.flush(buffers=buffers, body_upserts=[])


    assert len(tool_layer_repo.l3_many_calls) == 1
    assert len(tool_layer_repo.l4_many_calls) == 1
    assert len(tool_layer_repo.l5_many_calls) == 1
    assert len(tool_layer_repo.l3_many_calls[0]) == 1
    assert len(tool_layer_repo.l4_many_calls[0]) == 1
    assert len(tool_layer_repo.l5_many_calls[0]) == 1
    assert len(tool_layer_repo.l3_calls) == 0
    assert len(tool_layer_repo.l4_calls) == 0
    assert len(tool_layer_repo.l5_calls) == 0


def test_enrich_engine_l3_needs_l5_does_not_escalate_scope_in_l3() -> None:
    """l3_lane에서는 NEEDS_L5 파일도 LSP 호출 없이 즉시 DONE 처리 — scope escalation은 l5_lane에서 발생한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackend("ERR_LSP_DOCUMENT_SYMBOL_FAILED: reason=No workspace contains /repo/a.py"),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j1",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level=None,
        scope_root=None,
        scope_attempts=0,
    )

    result = engine._process_single_l3_job(job)

    # l3_lane: LSP extract 없이 즉시 DONE (scope escalation은 l5_lane에서 처리)
    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert len(queue_repo.calls) == 0  # scope escalation 없음
    assert engine._schedule_l1_probe_after_l3_fallback_called == 0


def test_enrich_engine_l3_preprocess_can_skip_lsp_extract() -> None:
    """전처리 결과가 충분하면 LSP extract 없이 TOOL_READY로 완료해야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackendShouldNotBeCalled(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )

    class _StubPreprocessService:
        def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int = 0) -> L3PreprocessResultDTO:
            _ = (relative_path, content_text, max_bytes)
            return L3PreprocessResultDTO(
                symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 1}],
                degraded=False,
                decision=L3PreprocessDecision.L3_ONLY,
                source="tree_sitter",
                reason="l3_preprocess_only",
            )

    engine._l3_preprocess_service = _StubPreprocessService()
    engine._l3_degraded_fallback_service = None
    engine._l3_preprocess_max_bytes = 1024
    job = FileEnrichJobDTO(
        job_id="j-preprocess-skip",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    result = engine._process_single_l3_job(job)

    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert result.lsp_update is not None
    assert len(result.lsp_update.symbols) == 1


def test_l3_preprocess_large_file_marks_deferred_heavy() -> None:
    """large file은 DEFERRED_HEAVY로 표기되어 배치 LSP 경로를 피해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "a" * (300 * 1024)

    result = service.preprocess(relative_path="repo_a/src/a.py", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.DEFERRED_HEAVY
    assert result.decision is not L3PreprocessDecision.L3_ONLY
    assert result.reason == "l3_preprocess_large_file"


def test_l3_preprocess_single_symbol_routes_to_needs_l5() -> None:
    """심볼 1개만 있는 저신뢰 파일은 NEEDS_L5로 분류해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "def alpha():\n    return 1\n"

    result = service.preprocess(relative_path="repo_a/src/a.py", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.reason == "l3_preprocess_low_confidence"
    assert len(result.symbols) == 1


def test_l3_preprocess_single_symbol_config_filename_stays_l3_only() -> None:
    """config/settings 계열 파일은 단일 심볼이어도 L3_ONLY로 유지해 L5 과승격을 줄여야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "def load_settings():\n    return {}\n"

    result = service.preprocess(relative_path="repo_a/src/app_config.py", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert result.reason == "l3_preprocess_only"
    assert len(result.symbols) == 1


def test_l3_preprocess_single_symbol_regular_filename_still_needs_l5() -> None:
    """일반 파일은 기존 임계값(심볼 2 미만) 규칙을 계속 적용해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "def load_data():\n    return {}\n"

    result = service.preprocess(relative_path="repo_a/src/service.py", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.reason == "l3_preprocess_low_confidence"
    assert len(result.symbols) == 1


def test_l3_preprocess_multi_symbol_with_import_stays_l3_only() -> None:
    """심볼이 충분한 파일은 import가 있어도 기본적으로 L3_ONLY를 유지해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "import os\n\ndef alpha():\n    return 1\n\ndef beta():\n    return 2\n"

    result = service.preprocess(relative_path="repo_a/src/a.py", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert result.reason == "l3_preprocess_only"
    assert len(result.symbols) == 2


def test_l3_preprocess_query_budget_exceeded_routes_to_needs_l5(monkeypatch: pytest.MonkeyPatch) -> None:
    """query budget 초과 시 degraded + NEEDS_L5로 분기해야 한다."""
    service = L3TreeSitterPreprocessService(query_budget_ms=1.0, tree_sitter_enabled=False)
    ticks = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr("sari.services.collection.l3.l3_treesitter_preprocess_service.time.perf_counter", lambda: next(ticks))
    result = service.preprocess(relative_path="repo_a/src/a.py", content_text="def alpha():\n    return 1\n", max_bytes=1024)
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.degraded is True
    assert result.reason == "l3_query_budget_exceeded"


def test_l3_preprocess_compile_budget_exceeded_routes_to_needs_l5(monkeypatch: pytest.MonkeyPatch) -> None:
    """compile budget 초과 시 degraded + NEEDS_L5로 분기해야 한다."""
    service = L3TreeSitterPreprocessService(query_compile_ms_budget=1.0, tree_sitter_enabled=False)
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr("sari.services.collection.l3.l3_treesitter_preprocess_service.time.perf_counter", lambda: next(ticks))
    result = service.preprocess(relative_path="repo_a/src/a.py", content_text="def alpha():\n    return 1\n", max_bytes=1024)
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.degraded is True
    assert result.reason == "l3_query_compile_budget_exceeded"


def test_l3_preprocess_tree_sitter_single_symbol_routes_to_needs_l5() -> None:
    """tree-sitter라도 단일 심볼 저신뢰 케이스는 NEEDS_L5로 분류해야 한다."""

    class _StubTreeSitterExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            return lang_key == "py"

        def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float):  # noqa: ANN001
            _ = (lang_key, content_text, budget_sec)
            return type(
                "TreeSitterResult",
                (),
                {
                    "symbols": [{"name": "alpha", "kind": "function", "line": 1, "end_line": 2, "symbol_key": "alpha:1"}],
                    "degraded": False,
                    "reason": None,
                },
            )()

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_StubTreeSitterExtractor(),
    )
    result = service.preprocess(relative_path="repo_a/src/a.py", content_text="def alpha():\n    return 1\n", max_bytes=1024)
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.source == "tree_sitter_outline"
    assert result.reason == "l3_preprocess_low_confidence"
    assert len(result.symbols) == 1


def test_l3_preprocess_vue_low_symbol_routes_to_needs_l5() -> None:
    """Vue SFC는 소수 심볼만 추출되면 L5 보강 대상으로 승격해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "\n".join(
        [
            "<template><div>{{ message }}</div></template>",
            "<script setup lang=\"ts\">",
            "function alpha() { return 1 }",
            "function beta() { return 2 }",
            "</script>",
        ]
    )

    result = service.preprocess(relative_path="repo_a/src/App.vue", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.reason == "l3_preprocess_low_confidence"
    assert result.source == "regex_outline"
    assert len(result.symbols) == 2


def test_l3_preprocess_vue_high_symbol_stays_l3_only() -> None:
    """Vue SFC라도 심볼이 충분하면 L3_ONLY를 유지해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    script_lines = [f"function f{i}() {{ return {i} }}" for i in range(1, 12)]
    content = "\n".join(
        [
            "<template><div>ok</div></template>",
            "<script setup lang=\"ts\">",
            *script_lines,
            "</script>",
        ]
    )

    result = service.preprocess(relative_path="repo_a/src/App.vue", content_text=content, max_bytes=4096)

    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert result.source == "regex_outline"
    assert len(result.symbols) == 11


def test_l3_preprocess_tsls_group_shortcuts_to_needs_l5_without_symbols() -> None:
    """TSLS 그룹(ts/js)은 L3 파싱 없이 L5 fast-path로 직행해야 한다."""
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "export function alpha() { return 1 }\n"

    result = service.preprocess(relative_path="repo_a/src/main.ts", content_text=content, max_bytes=1024)

    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.source == "none"
    assert result.reason == "l3_preprocess_tsls_fast_path"
    assert result.degraded is False
    assert result.symbols == []


def test_l3_preprocess_tree_sitter_degraded_uses_regex_fallback_before_l5() -> None:
    """tree-sitter degraded 시 즉시 승격하지 않고 regex fallback 결과를 우선 사용한다."""

    class _StubTreeSitterExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            return lang_key == "py"

        def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float):  # noqa: ANN001
            _ = (lang_key, content_text, budget_sec)
            return type(
                "TreeSitterResult",
                (),
                {
                    "symbols": [],
                    "degraded": True,
                    "reason": "tree_sitter_budget_exceeded",
                },
            )()

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_StubTreeSitterExtractor(),
    )
    result = service.preprocess(relative_path="repo_a/src/a.py", content_text="def alpha():\n    return 1\n", max_bytes=1024)
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.degraded is True
    assert result.source == "regex_outline"
    assert result.reason == "tree_sitter_budget_exceeded"


def test_l3_preprocess_tree_sitter_exception_returns_explicit_reason() -> None:
    """tree-sitter 예외 발생 시에도 전처리 결과 reason에 예외 타입이 명시되어야 한다."""

    class _StubTreeSitterExtractorRaises:
        def is_available_for(self, lang_key: str) -> bool:
            return lang_key == "py"

        def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float):  # noqa: ANN001
            _ = (lang_key, content_text, budget_sec)
            raise OSError("boom")

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_StubTreeSitterExtractorRaises(),
    )
    result = service.preprocess(relative_path="repo_a/src/a.py", content_text="def alpha():\n    return 1\n", max_bytes=1024)

    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.degraded is True
    assert result.source == "regex_outline"
    assert result.reason == "tree_sitter_outline_exception:OSError"


def test_enrich_engine_run_l3_preprocess_returns_explicit_exception_result_on_read_error() -> None:
    """파일 읽기 실패 시 None 대신 명시적 예외 reason을 가진 NEEDS_L5 결과를 반환해야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackendShouldNotBeCalled(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    engine._l3_preprocess_service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    engine._l3_preprocess_max_bytes = 1024

    job = FileEnrichJobDTO(
        job_id="j-preprocess-read-error",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="scan",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    file_row = type("FileRow", (), {"absolute_path": "/path/does/not/exist.py"})()

    result = engine._run_l3_preprocess(job=job, file_row=file_row)

    assert result is not None
    assert result.decision is L3PreprocessDecision.NEEDS_L5
    assert result.degraded is True
    assert result.source == "none"
    assert str(result.reason).startswith("l3_preprocess_exception:")


def test_enrich_engine_l3_preprocess_deferred_heavy_finishes_without_lsp() -> None:
    """DEFERRED_HEAVY 결정은 배치 L3에서 LSP를 호출하지 않고 defer queue로 보내야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackendShouldNotBeCalled(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )

    class _StubPreprocessService:
        def preprocess(self, *, relative_path: str, content_text: str, max_bytes: int = 0) -> L3PreprocessResultDTO:
            _ = (relative_path, content_text, max_bytes)
            return L3PreprocessResultDTO(
                symbols=[],
                degraded=True,
                decision=L3PreprocessDecision.DEFERRED_HEAVY,
                source="regex_outline",
                reason="l3_preprocess_large_file",
            )

    engine._l3_preprocess_service = _StubPreprocessService()
    engine._l3_degraded_fallback_service = None
    engine._l3_preprocess_max_bytes = 1024
    job = FileEnrichJobDTO(
        job_id="j-preprocess-heavy",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    result = engine._process_single_l3_job(job)

    assert result.finished_status == "PENDING"
    assert result.state_update is None
    assert result.lsp_update is None
    assert len(queue_repo.defer_calls) == 1
    assert str(queue_repo.defer_calls[0]["defer_reason"]).startswith("l5_defer:deferred_heavy:")


def test_enrich_engine_admission_admit_triggers_force_probe() -> None:
    """L4 admission이 L5를 허용하면 해당 파일 probe를 즉시 force 스케줄해야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()

    class _ProbeCaptureBackend(_NoopLspBackend):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, bool, str]] = []

        def schedule_probe_for_file(
            self,
            repo_root: str,
            relative_path: str,
            force: bool = False,
            trigger: str = "background",
        ) -> str:
            self.calls.append((repo_root, relative_path, bool(force), str(trigger)))
            return "scheduled"

    backend = _ProbeCaptureBackend()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=backend,
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    engine._l5_total_decisions = 0
    engine._l5_total_admitted = 0
    engine._l5_batch_decisions = 0
    engine._l5_batch_admitted = 0

    class _AdmitAllL4Service:
        def evaluate_batch(self, *, repo_root: str, language_key: str, total_rate: float, batch_rate: float, reason_code):  # noqa: ANN001
            _ = (repo_root, language_key, total_rate, batch_rate, reason_code)
            from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RequestMode

            return L4AdmissionDecisionDTO(
                admit_l5=True,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=None,
                budget_cost=1,
                cooldown_until=None,
                mode=L5RequestMode.BATCH,
                workspace_uid="ws",
            )

    engine._l4_admission_service = _AdmitAllL4Service()
    job = FileEnrichJobDTO(
        job_id="j-admit",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    decision = engine._evaluate_l5_admission_for_job(job, "python")

    assert decision is not None
    assert decision.admit_l5 is True
    assert backend.calls == [("/workspace", "repo_a/src/a.py", True, "l4_admission")]


def test_enrich_engine_l5_calls_per_min_per_lang_cap_rejects_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """언어별 분당 상한 도달 시 두 번째 admit 시도는 즉시 거절되어야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_NoopLspBackend(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    engine._l5_calls_per_min_per_lang_max = 1

    class _AdmitAllL4Service:
        def evaluate_batch(self, *, repo_root: str, language_key: str, total_rate: float, batch_rate: float, reason_code):  # noqa: ANN001
            _ = (repo_root, language_key, total_rate, batch_rate, reason_code)
            from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RequestMode

            return L4AdmissionDecisionDTO(
                admit_l5=True,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=None,
                budget_cost=1,
                cooldown_until=None,
                mode=L5RequestMode.BATCH,
                workspace_uid="ws",
            )

    engine._l4_admission_service = _AdmitAllL4Service()
    times = iter([100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr("sari.services.collection.enrich_engine.time.monotonic", lambda: next(times))
    job = FileEnrichJobDTO(
        job_id="j-admit-cap",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    first = engine._evaluate_l5_admission_for_job(job, "python")
    second = engine._evaluate_l5_admission_for_job(job, "python")
    assert first is not None and first.admit_l5 is True
    assert second is not None and second.admit_l5 is False
    assert second.reject_reason is L5RejectReason.PRESSURE_RATE_EXCEEDED
    metrics = engine.get_runtime_metrics()
    assert metrics["l5_reject_count_by_reject_reason_pressure_rate_exceeded"] == pytest.approx(1.0)


def test_enrich_engine_runtime_metrics_records_mode_not_allowed_reject() -> None:
    """L4 정책 거절 사유는 reject_reason별 런타임 메트릭으로 집계되어야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_NoopLspBackend(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )

    class _RejectL4Service:
        def evaluate_batch(self, *, repo_root: str, language_key: str, total_rate: float, batch_rate: float, reason_code):  # noqa: ANN001
            _ = (repo_root, language_key, total_rate, batch_rate, reason_code)
            from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason, L5RequestMode

            return L4AdmissionDecisionDTO(
                admit_l5=False,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=L5RejectReason.MODE_NOT_ALLOWED,
                budget_cost=1,
                cooldown_until=None,
                mode=L5RequestMode.BATCH,
                workspace_uid="ws",
            )

    engine._l4_admission_service = _RejectL4Service()
    job = FileEnrichJobDTO(
        job_id="j-reject-reason",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    decision = engine._evaluate_l5_admission_for_job(job, "python")
    assert decision is not None and decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.MODE_NOT_ALLOWED
    metrics = engine.get_runtime_metrics()
    assert metrics["l5_reject_count_by_reject_reason_mode_not_allowed"] == pytest.approx(1.0)


def test_enrich_engine_runtime_metrics_include_l5_cost_units_dimensions() -> None:
    """L5 decision budget_cost는 reason/language/workspace 축으로 누적되어야 한다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_NoopLspBackend(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )

    class _AdmitCostL4Service:
        def evaluate_batch(self, *, repo_root: str, language_key: str, total_rate: float, batch_rate: float, reason_code):  # noqa: ANN001
            _ = (repo_root, language_key, total_rate, batch_rate, reason_code)
            from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RequestMode

            return L4AdmissionDecisionDTO(
                admit_l5=True,
                reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
                reject_reason=None,
                budget_cost=7,
                cooldown_until=None,
                mode=L5RequestMode.BATCH,
                workspace_uid="ws",
            )

    engine._l4_admission_service = _AdmitCostL4Service()
    job = FileEnrichJobDTO(
        job_id="j-cost-metrics",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    decision = engine._evaluate_l5_admission_for_job(job, "python")
    assert decision is not None and decision.admit_l5 is True

    metrics = engine.get_runtime_metrics()
    assert metrics["l5_cost_units_total_by_reason_L5_REASON_GOLDENSET_COVERAGE"] == pytest.approx(7.0)
    assert metrics["l5_cost_units_total_by_language_python"] == pytest.approx(7.0)
    assert metrics["l5_cost_units_total_by_workspace_/workspace"] == pytest.approx(7.0)


def test_enrich_engine_l3_needs_l5_finishes_without_extract_failure() -> None:
    """l3_lane에서 NEEDS_L5 파일은 LSP 호출 없이 즉시 DONE — extract 오류가 발생하지 않는다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackend("ERR_RPC_TIMEOUT: request timeout"),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j2",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level="module",
        scope_root="/workspace/repo_a/src",
        scope_attempts=0,
    )

    result = engine._process_single_l3_job(job)

    # l3_lane: LSP extract 없이 즉시 DONE
    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert len(queue_repo.calls) == 0
    assert engine._schedule_l1_probe_after_l3_fallback_called == 0


def test_enrich_engine_l3_broker_error_string_does_not_trigger_l3_defer() -> None:
    """l3_lane에서는 broker lease 오류 발생 없이 즉시 DONE — defer는 l5_lane에서 처리된다."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackend(
            "ERR_LSP_BROKER_LEASE_REQUIRED: lang=java, scope=/workspace/repo_a, lane=backlog, reason=budget_blocked"
        ),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j-broker-defer",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=3,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level="module",
        scope_root="/workspace/repo_a",
        scope_attempts=0,
    )

    result = engine._process_single_l3_job(job)

    # l3_lane: LSP extract 없이 즉시 DONE (broker defer는 l5_lane에서 발생)
    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert len(queue_repo.defer_calls) == 0
    assert len(queue_repo.calls) == 0
    assert engine._schedule_l1_probe_after_l3_fallback_called == 0


def test_enrich_engine_l3_wrapped_broker_error_also_skips_to_l5_candidate() -> None:
    """l3_lane에서 래핑된 broker 오류도 발생하지 않음 — 즉시 DONE."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackend(
            "LSP 추출 실패: ERR_LSP_BROKER_LEASE_REQUIRED: "
            "lang=java, scope=/workspace/repo_a, lane=backlog, reason=cooldown"
        ),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j-broker-defer-wrapped",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=1,
        last_error=None,
        next_retry_at=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level="module",
        scope_root="/workspace/repo_a",
        scope_attempts=0,
    )

    result = engine._process_single_l3_job(job)

    # l3_lane: LSP extract 없이 즉시 DONE
    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert len(queue_repo.defer_calls) == 0


def test_enrich_engine_l3_scope_trigger_message_does_not_fail_when_extract_removed() -> None:
    """l3_lane에서 scope trigger 오류가 발생하지 않음 — 즉시 DONE (scope escalation은 l5_lane에서)."""
    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_StubExtractBackend("ERR_LSP_WORKSPACE_MISMATCH: No workspace contains /repo/a.py"),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j3",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level="workspace",
        scope_root="/workspace",
        scope_attempts=2,
    )

    result = engine._process_single_l3_job(job)

    # l3_lane: LSP extract 없이 즉시 DONE
    assert result.finished_status == "DONE"
    assert result.failure_update is None
    assert len(queue_repo.calls) == 0


def test_enrich_engine_records_scope_learning_after_l3_success() -> None:
    """L3 성공 시 backend가 제공하면 scope learning hook을 호출해야 한다."""

    class _CaptureScopeLearningBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []

        def record_scope_override_success(self, *, repo_root: str, relative_path: str, scope_root: str, scope_level: str) -> None:
            self.calls.append((repo_root, relative_path, scope_root, scope_level))

    backend = _CaptureScopeLearningBackend()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(lsp_backend=backend, queue_repo=_CaptureEscalateQueueRepo(), error_policy=error_policy)
    job = FileEnrichJobDTO(
        job_id="j4",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=1,
        enqueue_source="l3",
        status="DONE",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        scope_level="repo",
        scope_root="/workspace/repo_a",
        scope_attempts=1,
    )
    engine._record_scope_learning_after_l3_success(job=job)
    assert backend.calls == [("/workspace", "repo_a/src/a.py", "/workspace/repo_a", "repo")]


def test_scope_escalation_trigger_taxonomy_baseline() -> None:
    """Phase1 baseline taxonomy에 해당하는 오류만 escalation trigger여야 한다."""
    fn = getattr(file_collection_service_module, "_is_scope_escalation_trigger_error")
    assert fn("ERR_LSP_WORKSPACE_MISMATCH", "No Elm workspace contains /repo/x") is True
    assert fn("ERR_CONFIG_INVALID", "project model missing") is True


def test_enrich_engine_orders_l3_groups_using_backend_sort_key() -> None:
    """PR3 baseline: backend sort key가 제공되면 L3 그룹 순서를 lane-aware 힌트로 재정렬해야 한다."""

    class _SortBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []

        def get_l3_group_sort_key(self, *, repo_root: str, sample_relative_path: str, group_size: int):
            self.calls.append((repo_root, sample_relative_path, group_size))
            # repo_b를 우선 배치
            if "repo_b" in sample_relative_path:
                return (0, 0, -1.0, "b")
            return (1, 1, 0.0, "a")

    engine = object.__new__(EnrichEngine)
    engine._lsp_backend = _SortBackend()

    def _resolve_lang(_p: str):  # noqa: ANN001
        return Language.PYTHON

    engine._resolve_lsp_language = _resolve_lang  # type: ignore[method-assign]
    jobs = [
        FileEnrichJobDTO(
            job_id="j1",
            repo_id="r1",
            repo_root="/workspace/repo_a",
            relative_path="repo_a/a.py",
            content_hash="h1",
            priority=1,
            enqueue_source="l3",
            status="PENDING",
            attempt_count=0,
            last_error=None,
            next_retry_at="2026-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        FileEnrichJobDTO(
            job_id="j2",
            repo_id="r2",
            repo_root="/workspace/repo_b",
            relative_path="repo_b/b.py",
            content_hash="h2",
            priority=1,
            enqueue_source="l3",
            status="PENDING",
            attempt_count=0,
            last_error=None,
            next_retry_at="2026-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    ]
    groups = engine._group_jobs_by_repo_and_language(jobs)
    ordered = engine._order_l3_groups_for_scheduling(groups)
    assert ordered[0][0].relative_path == "repo_b/b.py"
    assert ordered[1][0].relative_path == "repo_a/a.py"


def test_solid_lsp_backend_group_sort_key_prefers_profiled_hot_active_scope() -> None:
    """PR3 baseline: profiled 언어에서는 active-scope reuse와 hotness가 정렬 우선순위에 반영돼야 한다."""

    class _FakeHub:
        def resolve_language(self, _relative_path: str) -> Language:
            return Language.JAVA

    broker = LspSessionBroker(
        profiles={
            "java": LspBrokerLanguageProfile(
                language="java",
                hot_lanes=1,
                backlog_lanes=1,
                sticky_idle_ttl_sec=10.0,
                switch_cooldown_sec=0.0,
                min_lease_ms=0,
            )
        },
        max_standby_sessions_per_lang=1,
        max_standby_sessions_per_budget_group=1,
        now_monotonic=time.monotonic,
    )
    tracker = WatcherHotnessTracker(now_monotonic=time.monotonic)
    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    backend.configure_session_runtime(session_broker=broker, watcher_hotness_tracker=tracker, enabled=True)
    # scope planner off (default): runtime scope == repo_root
    tracker.record_fs_event(
        event_type="modified",
        repo_root="/workspace",
        relative_path="repo_hot/src/A.java",
        language=Language.JAVA,
        lsp_scope_root="/workspace/repo_hot",
    )
    with broker.lease(
        language=Language.JAVA,
        lsp_scope_root="/workspace/repo_hot",
        lane="backlog",
        hotness_score=3.0,
        pending_jobs_in_scope=1,
    ):
        hot_key = backend.get_l3_group_sort_key(
            repo_root="/workspace/repo_hot",
            sample_relative_path="src/A.java",
            group_size=10,
        )
        cold_key = backend.get_l3_group_sort_key(
            repo_root="/workspace/repo_cold",
            sample_relative_path="src/B.java",
            group_size=10,
        )
    assert hot_key < cold_key


def test_solid_lsp_backend_profiled_parallelism_and_bulk_mode_do_not_bypass_broker() -> None:
    """PR3 baseline: profiled 언어에서는 backend 병렬도/벌크모드가 hub scale-out 경로를 직접 열지 않아야 한다."""

    class _FakeHub:
        def __init__(self) -> None:
            self.acquire_pool_calls = 0
            self.set_bulk_mode_calls = 0
            self.get_running_calls = 0
            self.prewarm_calls = 0

        def acquire_pool(self, **kwargs):  # noqa: ANN003
            self.acquire_pool_calls += 1
            return [object()]

        def set_bulk_mode(self, **kwargs):  # noqa: ANN003
            self.set_bulk_mode_calls += 1

        def get_running_instance_count(self, **kwargs):  # noqa: ANN003
            self.get_running_calls += 1
            return 2

        def prewarm_language_pool(self, **kwargs):  # noqa: ANN003
            self.prewarm_calls += 1

        def get_metrics(self) -> dict[str, int]:
            return {}

    broker = LspSessionBroker(
        profiles={
            "java": LspBrokerLanguageProfile(
                language="java",
                hot_lanes=1,
                backlog_lanes=1,
                sticky_idle_ttl_sec=10.0,
                switch_cooldown_sec=0.0,
                min_lease_ms=0,
            )
        },
        max_standby_sessions_per_lang=1,
        max_standby_sessions_per_budget_group=1,
        now_monotonic=time.monotonic,
    )
    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_session_runtime(session_broker=broker, watcher_hotness_tracker=None, enabled=True)

    assert backend.get_parallelism("/workspace", Language.JAVA) == 1
    assert backend.get_parallelism_for_batch("/workspace", Language.JAVA, 8) == 1
    backend.set_bulk_mode("/workspace", Language.JAVA, True)

    assert hub.get_running_calls == 0
    assert hub.prewarm_calls == 0
    assert hub.acquire_pool_calls == 0
    assert hub.set_bulk_mode_calls == 0
    assert backend.get_runtime_metrics().get("broker_parallelism_guard_skip_count", 0) >= 3


def test_scope_escalation_next_level_ladder() -> None:
    """scope escalation 단계는 module -> repo -> workspace -> stop 이어야 한다."""
    fn = getattr(file_collection_service_module, "_next_scope_level_for_escalation")
    assert fn("module") == "repo"
    assert fn("repo") == "workspace"
    assert fn("workspace") is None
    assert fn(None) == "repo"


def test_l3_extract_failure_kind_classification_phase1() -> None:
    """PR-B baseline 3종 분류는 L3 extract 오류 메시지를 안정적으로 분류해야 한다."""
    fn = classify_l3_extract_failure_kind
    assert fn("ERR_LSP_SERVER_MISSING: command not found") == "PERMANENT_UNAVAILABLE"
    assert fn("ERR_CONFIG_INVALID: project model missing") == "PERMANENT_UNAVAILABLE"
    assert fn("ERR_LSP_WORKSPACE_MISMATCH: no workspace contains /x") == "PERMANENT_UNAVAILABLE"
    assert fn("ERR_RPC_TIMEOUT: request timeout") == "TRANSIENT_FAIL"
    assert fn("ERR_BROKEN_PIPE: broken pipe") == "TRANSIENT_FAIL"
    assert fn("ERR_SERVER_EXITED: server exited") == "TRANSIENT_FAIL"


class _CaptureHotLanguageBackend(_NoopLspBackend):
    """prewarm 상위 언어 설정을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.repo_root: str | None = None
        self.languages: set[Language] = set()

    def configure_hot_languages(self, repo_root: str, languages: set[Language]) -> None:
        """설정된 저장소/언어 목록을 보관한다."""
        self.repo_root = repo_root
        self.languages = set(languages)


class _DirtySink:
    """후보 인덱스 dirty 호출을 추적하는 테스트 더블이다."""

    def __init__(self) -> None:
        """호출 카운터를 초기화한다."""
        self.repo_calls: int = 0
        self.file_calls: int = 0
        self.upsert_calls: int = 0
        self.delete_calls: int = 0

    def mark_repo_dirty(self, repo_root: str) -> None:
        """저장소 단위 dirty 호출 횟수를 증가시킨다."""
        _ = repo_root
        self.repo_calls += 1

    def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
        """파일 단위 dirty 호출 횟수를 증가시킨다."""
        _ = (repo_root, relative_path)
        self.file_calls += 1

    def record_upsert(self, change: CandidateIndexChangeDTO) -> None:
        """파일 upsert 이벤트 호출 횟수를 증가시킨다."""
        _ = change
        self.upsert_calls += 1

    def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
        """파일 delete 이벤트 호출 횟수를 증가시킨다."""
        _ = (repo_root, relative_path, reason)
        self.delete_calls += 1


class _CaptureVectorSink:
    """벡터 임베딩 upsert 호출을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        """호출 인자를 기록한다."""
        self.calls.append((repo_root, relative_path, content_hash, content_text))


class _FailingVectorSink:
    """벡터 임베딩 업서트 실패를 발생시키는 테스트 더블이다."""

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        """항상 명시적 런타임 오류를 발생시킨다."""
        del repo_root, relative_path, content_hash, content_text
        raise RuntimeError("vector write failed")


class _CaptureCandidateService:
    """오케스트레이터 입력 워크스페이스를 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        """초기 캡처 상태를 준비한다."""
        self.last_workspaces: list[WorkspaceDTO] = []

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> CandidateSearchResultDTO:
        """입력 워크스페이스를 저장하고 빈 결과를 반환한다."""
        del query, limit
        self.last_workspaces = list(workspaces)
        return CandidateSearchResultDTO(candidates=[], source="scan", errors=[])

    def filter_workspaces_by_repo(self, workspaces: list[WorkspaceDTO], repo_root: str) -> list[WorkspaceDTO]:
        """repo 필터 정책을 실제 서비스와 동일하게 모사한다."""
        return [workspace for workspace in workspaces if workspace.path == repo_root]


class _NoopSymbolService:
    """빈 해석 결과를 반환하는 테스트 더블이다."""

    def resolve(self, candidates: list[object], query: str, limit: int) -> tuple[list[object], list[object]]:
        """빈 결과를 반환한다."""
        del candidates, query, limit
        return [], []


def test_solid_lsp_extraction_backend_accepts_document_symbol_without_relative_path() -> None:
    """documentSymbol에 relativePath가 없어도 심볼을 추출해야 한다."""

    class _Symbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "alpha",
                    "kind": "function",
                    "location": {
                        "range": {
                            "start": {"line": 3},
                            "end": {"line": 8},
                        }
                    },
                }
            ]

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str) -> _Symbols:
            del relative_path
            return _Symbols()

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="a.py", content_hash="h")
    assert result.error_message is None
    assert len(result.symbols) == 1
    assert result.symbols[0]["name"] == "alpha"
    assert isinstance(result.symbols[0]["symbol_key"], str)
    assert result.symbols[0]["parent_symbol_key"] is None
    assert int(result.symbols[0]["depth"]) == 0


def test_solid_lsp_extraction_backend_requests_document_symbols_without_sync_when_supported() -> None:
    """L3 extract는 지원 시 sync_with_ls=False로 documentSymbol을 요청해야 한다."""

    class _Symbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [{"name": "alpha", "kind": "function", "location": {"range": {"start": {"line": 1}, "end": {"line": 1}}}}]

    class _CaptureLsp:
        def __init__(self) -> None:
            self.flags: list[bool] = []

        def request_document_symbols(self, relative_path: str, *, sync_with_ls: bool = True) -> _Symbols:
            del relative_path
            self.flags.append(bool(sync_with_ls))
            return _Symbols()

    class _FakeHub:
        def __init__(self) -> None:
            self.lsp = _CaptureLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _CaptureLsp:
            del language, repo_root, request_kind
            return self.lsp

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="a.py", content_hash="h")

    assert result.error_message is None
    assert hub.lsp.flags == [False]


def test_solid_lsp_extraction_backend_dedupes_inflight_same_request() -> None:
    """동일 (repo,path,hash) 동시 요청은 LSP 1회 호출로 병합해야 한다."""

    class _Symbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [{"name": "alpha", "kind": "function", "location": {"range": {"start": {"line": 1}, "end": {"line": 1}}}}]

    class _FakeLsp:
        def __init__(self) -> None:
            self.calls = 0

        def request_document_symbols(self, relative_path: str) -> _Symbols:
            del relative_path
            time.sleep(0.05)
            self.calls += 1
            return _Symbols()

    class _FakeHub:
        def __init__(self) -> None:
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return self.lsp

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]

    def _run_extract() -> LspExtractionResultDTO:
        return backend.extract(repo_root="/repo", relative_path="a.py", content_hash="h1")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_run_extract)
        second_future = executor.submit(_run_extract)
        first = first_future.result()
        second = second_future.result()

    assert first.error_message is None
    assert second.error_message is None
    assert hub.lsp.calls == 1


def test_solid_lsp_extraction_backend_probe_schedule_dedupes_inflight() -> None:
    """동일 key probe는 inflight 중복 submit을 허용하지 않아야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path
            time.sleep(0.05)

            class _Symbols:
                def iter_symbols(self) -> list[dict[str, object]]:
                    return []

            return _Symbols()

    class _FakeHub:
        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            time.sleep(0.05)
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    first = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")
    second = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")
    backend.shutdown_probe_executor()

    assert first == "scheduled"
    assert second in {"inflight", "ready"}


def test_solid_lsp_extraction_backend_force_does_not_double_submit_when_inflight() -> None:
    """force 요청도 inflight가 있으면 추가 submit하지 않아야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path
            time.sleep(0.05)

            class _Symbols:
                def iter_symbols(self) -> list[dict[str, object]]:
                    return []

            return _Symbols()

    class _FakeHub:
        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub(), force_join_ms=0)  # type: ignore[arg-type]
    first = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")
    forced = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py", force=True, trigger="force")
    backend.shutdown_probe_executor()

    assert first == "scheduled"
    assert forced in {"inflight", "starting", "ready"}


def test_solid_lsp_extraction_backend_force_returns_ready_after_recent_success() -> None:
    """probe가 최근 성공(READY) 상태면 force도 재제출 대신 ready를 반환해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path

            class _Symbols:
                def iter_symbols(self) -> list[dict[str, object]]:
                    return []

            return _Symbols()

    class _FakeHub:
        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub(), force_join_ms=0)  # type: ignore[arg-type]
    first = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")
    deadline = time.monotonic() + 1.0
    while backend.is_probe_inflight_for_file(repo_root="/repo", relative_path="a.py") and time.monotonic() < deadline:
        time.sleep(0.001)
    forced = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py", force=True, trigger="force")
    backend.shutdown_probe_executor()

    assert first == "scheduled"
    assert forced == "ready"


def test_solid_lsp_extraction_backend_warming_reschedules_after_next_retry() -> None:
    """WARMING 상태라도 next_retry가 지나면 재스케줄되어야 한다."""

    class _FakeHub:
        def __init__(self) -> None:
            self.calls = 0

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            self.calls += 1

            class _FakeLsp:
                def request_document_symbols(self, relative_path: str):  # noqa: ANN001
                    del relative_path

                    class _Symbols:
                        def iter_symbols(self) -> list[dict[str, object]]:
                            return []

                    return _Symbols()

            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub(), probe_workers=1, l1_workers=1)  # type: ignore[arg-type]
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    now = time.monotonic()
    with backend._probe_lock:
        backend._probe_state[key] = file_collection_service_module._ProbeStateRecord(  # type: ignore[attr-defined]
            status="WARMING",
            warming_count=1,
            next_retry_monotonic=now - 0.01,
            last_seen_monotonic=now,
        )
    result = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py")
    backend.shutdown_probe_executor()
    assert result == "scheduled"


def test_solid_lsp_extraction_backend_records_last_trigger_on_schedule() -> None:
    """probe 스케줄 시 trigger가 상태에 보존되어야 한다."""

    class _FakeHub:
        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            time.sleep(0.02)

            class _FakeLsp:
                def request_document_symbols(self, relative_path: str):  # noqa: ANN001
                    del relative_path

                    class _Symbols:
                        def iter_symbols(self) -> list[dict[str, object]]:
                            return []

                    return _Symbols()

            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub(), probe_workers=1, l1_workers=1)  # type: ignore[arg-type]
    scheduled = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.py", trigger="bootstrap")
    deadline = time.monotonic() + 1.0
    while backend.is_probe_inflight_for_file(repo_root="/repo", relative_path="a.py") and time.monotonic() < deadline:
        time.sleep(0.005)
    key = (str(Path("/repo").resolve()), Language.PYTHON)
    with backend._probe_lock:
        state = backend._probe_state.get(key)
        last_trigger = None if state is None else getattr(state, "last_trigger", None)
    backend.shutdown_probe_executor()

    assert scheduled == "scheduled"
    assert last_trigger == "bootstrap"


def test_solid_lsp_extraction_backend_schedules_java_background_probe() -> None:
    """배치 throughput 플래그 제거 이후 Java background probe는 기본 스케줄되어야 한다."""

    class _FakeHub:
        pass

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    backend.configure_session_runtime(
        session_broker=None,
        watcher_hotness_tracker=None,
        enabled=False,
    )

    scheduled = backend.schedule_probe_for_file(
        repo_root="/repo",
        relative_path="src/main/java/App.java",
        trigger="background",
    )

    assert scheduled == "scheduled"


def test_solid_lsp_extraction_backend_prewarm_allows_parallel_for_different_keys() -> None:
    """서로 다른 (repo, language) key는 prewarm을 병렬 수행할 수 있어야 한다."""

    class _FakeHub:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self.lock = threading.Lock()
            self.gate = threading.Event()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            _ = (language, repo_root)
            with self.lock:
                self.active += 1
                self.calls += 1
                if self.active > self.max_active:
                    self.max_active = self.active
            self.gate.wait(timeout=1.0)
            with self.lock:
                self.active -= 1

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(backend._ensure_prewarm, Language.PYTHON, "/repo-a")
        second = executor.submit(backend._ensure_prewarm, Language.GO, "/repo-b")
        deadline = time.monotonic() + 1.0
        while hub.max_active < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        hub.gate.set()
        first.result()
        second.result()

    assert hub.calls == 2
    assert hub.max_active >= 2


def test_solid_lsp_extraction_backend_probe_inflight_persists_until_l1_finishes() -> None:
    """L1 probe가 끝날 때까지 inflight 상태를 유지해야 한다."""

    class _Symbols:
        def __init__(self, started: threading.Event, gate: threading.Event) -> None:
            self._started = started
            self._gate = gate

        def iter_symbols(self) -> list[dict[str, object]]:
            self._started.set()
            self._gate.wait(timeout=1.0)
            return []

    class _FakeLsp:
        def __init__(self, started: threading.Event, gate: threading.Event) -> None:
            self._started = started
            self._gate = gate

        def request_document_symbols(self, relative_path: str) -> _Symbols:
            del relative_path
            return _Symbols(self._started, self._gate)

    class _FakeHub:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.gate = threading.Event()

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> _FakeLsp:
            del language, repo_root, request_kind
            return _FakeLsp(self.started, self.gate)

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub, probe_workers=1, l1_workers=1)  # type: ignore[arg-type]
    scheduled = backend.schedule_probe_for_file(repo_root="/repo", relative_path="a.go")
    assert scheduled == "scheduled"

    assert hub.started.wait(timeout=1.0)
    assert backend.is_probe_inflight_for_file(repo_root="/repo", relative_path="a.go") is True

    hub.gate.set()
    deadline = time.monotonic() + 1.0
    while backend.is_probe_inflight_for_file(repo_root="/repo", relative_path="a.go") and time.monotonic() < deadline:
        time.sleep(0.005)
    backend.shutdown_probe_executor()

    assert backend.is_probe_inflight_for_file(repo_root="/repo", relative_path="a.go") is False


def _policy() -> CollectionPolicyDTO:
    """테스트 기본 수집 정책을 반환한다."""
    return CollectionPolicyDTO(
        include_ext=(".py",),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )


def test_file_collection_scan_once_skips_unchanged_read_bytes(tmp_path: Path, monkeypatch) -> None:
    """동일 파일 재스캔 시 본문 재읽기를 건너뛰어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "a.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )
    service.scan_once(str(repo_dir.resolve()))

    original_read_bytes = Path.read_bytes
    read_count = {"value": 0}

    def _tracked_read_bytes(path: Path) -> bytes:
        if path == target:
            read_count["value"] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _tracked_read_bytes)

    service.scan_once(str(repo_dir.resolve()))

    assert read_count["value"] == 0


def test_file_collection_scan_once_does_not_schedule_l1_probe(tmp_path: Path) -> None:
    """prewarm disabled 시 L1 스캔 경로는 probe를 직접 스케줄하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l1-no-probe"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    backend = _ProbeCountingBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        policy_repo=None,
        event_repo=None,
        lsp_probe_scan_prewarm_enabled=False,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert backend.calls == []


def test_file_collection_scan_once_schedules_bootstrap_probe_when_prewarm_enabled(tmp_path: Path) -> None:
    """Wave 1: lsp_probe_scan_prewarm_enabled=True 시 scan이 bootstrap probe를 스케줄해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-prewarm-wave1"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    backend = _ProbeCountingBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        policy_repo=None,
        event_repo=None,
        lsp_probe_scan_prewarm_enabled=True,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert len(backend.calls) >= 1


def test_configure_lsp_prewarm_languages_schedules_wave2_probe() -> None:
    """Wave 2: hot 언어 확정 후 대표 파일로 probe를 스케줄해야 한다."""
    from sari.services.collection.repo_support import CollectionRepoSupport
    from sari.core.models import CollectionPolicyDTO
    from sari.db.repositories.workspace_repository import WorkspaceRepository
    from solidlsp.ls_config import Language

    class _FakeWorkspaceRepo:
        def list_all(self):
            return []

    class _CapturingSchedulerBackend:
        def __init__(self):
            self.probe_calls: list[dict] = []

        def configure_hot_languages(self, *, repo_root: str, languages: set) -> None:
            pass

        def schedule_probe_for_file(
            self,
            *,
            repo_root: str,
            relative_path: str,
            force: bool = False,
            trigger: str = "background",
        ) -> str:
            self.probe_calls.append({"repo_root": repo_root, "relative_path": relative_path, "trigger": trigger})
            return "scheduled"

    backend = _CapturingSchedulerBackend()
    repo_support = CollectionRepoSupport(
        workspace_repo=_FakeWorkspaceRepo(),
        policy=CollectionPolicyDTO(
            include_ext=(".py",),
            exclude_globs=(),
            max_file_size_bytes=512 * 1024,
            scan_interval_sec=180,
            max_enrich_batch=20,
            retry_max_attempts=5,
            retry_backoff_base_sec=1,
            queue_poll_interval_ms=100,
        ),
        policy_repo=None,
        lsp_backend=backend,
        repo_registry_repo=None,
        lsp_prewarm_min_language_files=1,
        lsp_prewarm_top_language_count=2,
    )

    python_lang = Language.PYTHON
    language_counts = {python_lang: 5}
    language_sample_files = {python_lang: "src/main.py"}

    repo_support.configure_lsp_prewarm_languages(
        repo_root="/fake/repo",
        language_counts=language_counts,
        language_sample_files=language_sample_files,
    )

    wave2_calls = [c for c in backend.probe_calls if c.get("trigger") == "wave2_prewarm"]
    assert len(wave2_calls) == 1
    assert wave2_calls[0]["relative_path"] == "src/main.py"


def test_vector_embedding_runs_even_when_body_persistence_disabled(tmp_path: Path) -> None:
    """L2 본문 저장 비활성화 상태에서도 벡터 임베딩은 독립적으로 실행되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-vector"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    vector_sink = _CaptureVectorSink()
    body_repo = FileBodyRepository(db_path)

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=body_repo,
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
        vector_index_sink=vector_sink,
        persist_body_for_read=False,
    )
    service.scan_once(str(repo_dir.resolve()))
    processed = service.process_enrich_jobs(limit=50)

    assert processed >= 1
    assert len(vector_sink.calls) >= 1
    # L2 본문 저장은 비활성화되어야 한다.
    first = vector_sink.calls[0]
    assert body_repo.read_body_text(first[0], first[1], first[2]) is None


def test_tantivy_sync_index_skips_unchanged_file_read(tmp_path: Path, monkeypatch) -> None:
    """Tantivy 동기화는 변경 없는 파일을 다시 읽지 않아야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    original_read_bytes = Path.read_bytes
    read_count = {"value": 0}

    def _tracked_read_bytes(path: Path) -> bytes:
        if path == target:
            read_count["value"] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _tracked_read_bytes)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert read_count["value"] == 0


def test_tantivy_search_accepts_special_chars_query(tmp_path: Path) -> None:
    """특수문자 포함 질의도 파싱 실패 없이 처리되어야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("if (x > 0):\n    return x\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-special",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    items = backend.search(workspaces=[workspace], query="if (x > 0):", limit=10)

    assert len(items) >= 1


def test_tantivy_search_does_not_sync_every_request(tmp_path: Path, monkeypatch) -> None:
    """인덱스가 clean 상태이면 검색마다 전체 sync를 반복하지 않아야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-clean",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    sync_count = {"value": 0}
    original_sync_index = backend._sync_index

    def _tracked_sync(workspaces: list[WorkspaceDTO]) -> None:
        sync_count["value"] += 1
        original_sync_index(workspaces)

    monkeypatch.setattr(backend, "_sync_index", _tracked_sync)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert sync_count["value"] == 1


def test_search_orchestrator_prefilters_repo_before_candidate_search() -> None:
    """repo 지정 시 후보 검색 전 단계에서 워크스페이스가 축소되어야 한다."""

    class _WorkspaceRepo:
        """고정 워크스페이스 목록 저장소 더블이다."""

        def list_all(self) -> list[WorkspaceDTO]:
            """고정 워크스페이스 목록을 반환한다."""
            return [
                WorkspaceDTO(path="/repo-a", name="repo-a", indexed_at=None, is_active=True),
                WorkspaceDTO(path="/repo-b", name="repo-b", indexed_at=None, is_active=True),
            ]

    candidate = _CaptureCandidateService()
    orchestrator = SearchOrchestrator(
        workspace_repo=_WorkspaceRepo(),
        candidate_service=candidate,
        symbol_service=_NoopSymbolService(),
    )

    orchestrator.search(query="hello", limit=10, repo_root="/repo-a")

    assert len(candidate.last_workspaces) == 1
    assert candidate.last_workspaces[0].path == "/repo-a"


def test_sqlite_connect_works_after_wal_initialized(tmp_path: Path) -> None:
    """초기화 이후 일반 연결은 안정적으로 동작해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with connect(db_path) as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()

    assert row is not None
    assert str(row[0]).lower() == "wal"


def test_file_collection_scan_once_does_not_delete_file_seen_after_scan_start(tmp_path: Path) -> None:
    """스캔 도중 최근에 관측된 파일은 삭제 처리되면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    now = "2026-02-17T00:00:00+00:00"
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="r_repo",
            repo_root="/repo",
            relative_path="new.py",
            absolute_path="/repo/new.py",
            repo_label="repo",
            mtime_ns=1,
            size_bytes=1,
            content_hash="h",
            is_deleted=False,
            last_seen_at="2026-02-17T00:10:00+00:00",
            updated_at=now,
            enrich_state="PENDING",
        )
    )

    deleted = file_repo.mark_missing_as_deleted(
        repo_root="/repo",
        seen_relative_paths=[],
        updated_at=now,
        scan_started_at="2026-02-17T00:05:00+00:00",
    )
    row = file_repo.get_file(repo_root="/repo", relative_path="new.py")
    assert deleted == 0
    assert row is not None
    assert row.is_deleted is False


def test_mark_missing_as_deleted_handles_large_seen_paths_without_sql_variable_overflow(tmp_path: Path, monkeypatch) -> None:
    """seen 목록이 커도 SQLite 변수 한도 오류 없이 누락 파일만 삭제해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    now = "2026-02-17T00:00:00+00:00"
    scan_started_at = "2026-02-17T00:05:00+00:00"

    @contextmanager
    def _limited_connect(path: Path):
        with connect(path) as conn:
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            yield conn

    monkeypatch.setattr(file_collection_repository_module, "connect", _limited_connect)

    repo_root = "/repo"
    stale_to_delete = "stale.py"
    seen_keep = [f"seen_{i}.py" for i in range(1200)]

    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="r_repo",
            repo_root=repo_root,
            relative_path=stale_to_delete,
            absolute_path=f"{repo_root}/{stale_to_delete}",
            repo_label="repo",
            mtime_ns=1,
            size_bytes=1,
            content_hash="h-stale",
            is_deleted=False,
            last_seen_at="2026-02-17T00:00:01+00:00",
            updated_at=now,
            enrich_state="PENDING",
        )
    )
    for index, rel_path in enumerate(seen_keep):
        file_repo.upsert_file(
            CollectedFileL1DTO(
                repo_id="r_repo",
                repo_root=repo_root,
                relative_path=rel_path,
                absolute_path=f"{repo_root}/{rel_path}",
                repo_label="repo",
                mtime_ns=index + 2,
                size_bytes=1,
                content_hash=f"h-{index}",
                is_deleted=False,
                last_seen_at="2026-02-17T00:00:02+00:00",
                updated_at=now,
                enrich_state="PENDING",
            )
        )

    deleted = file_repo.mark_missing_as_deleted(
        repo_root=repo_root,
        seen_relative_paths=seen_keep,
        updated_at=now,
        scan_started_at=scan_started_at,
    )

    stale_row = file_repo.get_file(repo_root=repo_root, relative_path=stale_to_delete)
    seen_row = file_repo.get_file(repo_root=repo_root, relative_path=seen_keep[0])
    assert deleted == 1
    assert stale_row is not None
    assert stale_row.is_deleted is True
    assert seen_row is not None
    assert seen_row.is_deleted is False


def test_vector_embedding_failure_marks_job_failed(tmp_path: Path) -> None:
    """벡터 임베딩 실패는 실패 상태로 승격되어 재시도 대상이 되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-vector-fail"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        vector_index_sink=_FailingVectorSink(),
    )
    service.scan_once(str(repo_dir.resolve()))
    processed = service.process_enrich_jobs_l2(limit=50)

    assert processed >= 1
    state = FileCollectionRepository(db_path).get_file(str(repo_dir.resolve()), "a.py")
    assert state is not None
    assert state.enrich_state == "FAILED"


def test_enrich_l3_skips_recent_successful_same_hash(tmp_path: Path) -> None:
    """최근 성공 + 동일 hash면 L3 추출을 건너뛰어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-skip"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    class _CountingLspBackend(LspExtractionBackend):
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            self.calls += 1
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

    backend = _CountingLspBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        l3_recent_success_ttl_sec=3600,
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    file_row = FileCollectionRepository(db_path).get_file(repo_root=repo_root, relative_path="a.py")
    assert file_row is not None
    ToolReadinessRepository(db_path).upsert_state(
        ToolReadinessStateDTO(
            repo_root=repo_root,
            relative_path="a.py",
            content_hash=file_row.content_hash,
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=True,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason="ok",
            updated_at="2099-01-01T00:00:00+00:00",
        )
    )

    processed = service.process_enrich_jobs_l3(limit=10)

    assert processed >= 1
    assert backend.calls == 0


def test_enrich_l2_marks_l3_skipped_for_unsupported_extension(tmp_path: Path) -> None:
    """확장자 미지원 파일은 L2에서 L3_SKIPPED로 종료해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-skip-ext"
    repo_dir.mkdir()
    (repo_dir / "notes.txt").write_text("hello\n", encoding="utf-8")

    class _CountingLspBackend(LspExtractionBackend):
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            self.calls += 1
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

    backend = _CountingLspBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=CollectionPolicyDTO(
            include_ext=(".txt",),
            exclude_globs=("**/.git/**",),
            max_file_size_bytes=512 * 1024,
            scan_interval_sec=120,
            max_enrich_batch=100,
            retry_max_attempts=2,
            retry_backoff_base_sec=1,
            queue_poll_interval_ms=100,
        ),
        lsp_backend=backend,
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    _ = service.process_enrich_jobs_l3(limit=50)

    row = FileCollectionRepository(db_path).get_file(repo_root=repo_root, relative_path="notes.txt")
    assert row is not None
    assert row.enrich_state == "L3_SKIPPED"
    readiness = ToolReadinessRepository(db_path).get_state(repo_root=repo_root, relative_path="notes.txt")
    assert readiness is not None
    assert readiness.list_files_ready is True
    assert readiness.read_file_ready is True
    assert readiness.search_symbol_ready is False
    assert readiness.get_callers_ready is False
    assert readiness.tool_ready is False
    assert readiness.last_reason == "skip_unsupported_extension"
    assert backend.calls == 0


def test_enrich_l2_marks_l3_skipped_for_configured_unsupported_language(tmp_path: Path) -> None:
    """설정에서 제외한 언어는 L3를 수행하지 않고 L3_SKIPPED로 종료해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-skip-lang"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    class _CountingLspBackend(LspExtractionBackend):
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            self.calls += 1
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

    backend = _CountingLspBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        l3_supported_languages=("go", "java"),
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    _ = service.process_enrich_jobs_l3(limit=50)

    row = FileCollectionRepository(db_path).get_file(repo_root=repo_root, relative_path="a.py")
    assert row is not None
    assert row.enrich_state == "L3_SKIPPED"
    readiness = ToolReadinessRepository(db_path).get_state(repo_root=repo_root, relative_path="a.py")
    assert readiness is not None
    assert readiness.last_reason == "skip_unsupported_language"
    assert readiness.tool_ready is False
    assert backend.calls == 0


def test_enrich_engine_resolve_l3_skip_reason_reports_probe_check_error_explicitly() -> None:
    """probe availability checker 예외는 skip reason으로 명시되어야 한다."""

    class _ProbeCheckErrorBackend(_NoopLspBackend):
        def is_l3_permanently_unavailable_for_file(self, *, repo_root: str, relative_path: str) -> bool:
            _ = (repo_root, relative_path)
            raise OSError("probe checker failed")

    queue_repo = _CaptureEscalateQueueRepo()
    error_policy = _StubErrorPolicy()
    engine = build_min_enrich_engine_for_l3_test(
        lsp_backend=_ProbeCheckErrorBackend(),
        queue_repo=queue_repo,
        error_policy=error_policy,
    )
    job = FileEnrichJobDTO(
        job_id="j-skip-check-error",
        repo_id="r1",
        repo_root="/workspace",
        relative_path="repo_a/src/a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="scan",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    reason = engine._resolve_l3_skip_reason(job=job)

    assert reason == "skip_probe_check_error"


def test_enrich_l3_needs_l5_does_not_schedule_l3_fallback_probe(tmp_path: Path) -> None:
    """L3 extract 제거 후에는 l3_fallback probe 예약이 발생하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-fallback"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    class _FailingBackend(LspExtractionBackend):
        def __init__(self) -> None:
            self.scheduled: list[tuple[str, str, bool, str]] = []

        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            return LspExtractionResultDTO(symbols=[], relations=[], error_message="ERR_LSP_DOCUMENT_SYMBOL_FAILED: timeout")

        def is_probe_inflight_for_file(self, repo_root: str, relative_path: str) -> bool:
            del repo_root, relative_path
            return False

        def schedule_probe_for_file(self, repo_root: str, relative_path: str, force: bool = False, trigger: str = "background") -> str:
            self.scheduled.append((repo_root, relative_path, force, trigger))
            return "scheduled"

    backend = _FailingBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        lsp_probe_l1_languages=("py",),
        run_mode="prod",
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    _ = service.process_enrich_jobs_l3(limit=50)

    fallback_scheduled = [item for item in backend.scheduled if item[3] == "l3_fallback"]
    assert fallback_scheduled == []


def test_enrich_l3_failure_does_not_schedule_when_probe_inflight(tmp_path: Path) -> None:
    """이미 probe가 inflight면 l3_fallback 예약을 추가로 만들지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-fallback-inflight"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    class _FailingInflightBackend(LspExtractionBackend):
        def __init__(self) -> None:
            self.scheduled: list[tuple[str, str, bool, str]] = []

        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            return LspExtractionResultDTO(symbols=[], relations=[], error_message="ERR_LSP_DOCUMENT_SYMBOL_FAILED: timeout")

        def is_probe_inflight_for_file(self, repo_root: str, relative_path: str) -> bool:
            del repo_root, relative_path
            return True

        def schedule_probe_for_file(self, repo_root: str, relative_path: str, force: bool = False, trigger: str = "background") -> str:
            self.scheduled.append((repo_root, relative_path, force, trigger))
            return "scheduled"

    backend = _FailingInflightBackend()
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=backend,
        lsp_probe_l1_languages=("py",),
        run_mode="prod",
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    _ = service.process_enrich_jobs_l3(limit=50)

    fallback_scheduled = [item for item in backend.scheduled if item[3] == "l3_fallback"]
    assert fallback_scheduled == []


def test_enrich_l3_parallel_mode_no_longer_uses_extract_timeout_path(tmp_path: Path) -> None:
    """L3 extract 제거 후 병렬 timeout 실패 전이는 발생하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-l3-timeout"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo_dir / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")

    release = threading.Event()

    class _SlowBackend(LspExtractionBackend):
        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, content_hash
            if relative_path == "b.py":
                release.wait(timeout=1.0)
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)

        def get_parallelism(self, repo_root: str, language: Language) -> int:
            del repo_root, language
            return 2

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_SlowBackend(),
        l3_parallel_enabled=True,
        l3_executor_max_workers=2,
        run_mode="prod",
    )
    repo_root = str(repo_dir.resolve())
    service.scan_once(repo_root)
    _ = service.process_enrich_jobs_l2(limit=50)
    service._enrich_engine._l3_group_wait_timeout_sec = 0.05  # type: ignore[attr-defined]

    processed = service.process_enrich_jobs_l3(limit=50)
    release.set()

    assert processed >= 2
    queue_counts = FileEnrichQueueRepository(db_path).get_status_counts()
    assert queue_counts["RUNNING"] == 0
    assert queue_counts["FAILED"] == 0
    row_a = FileCollectionRepository(db_path).get_file(repo_root=repo_root, relative_path="a.py")
    row_b = FileCollectionRepository(db_path).get_file(repo_root=repo_root, relative_path="b.py")
    assert row_a is not None and row_b is not None
    assert "FAILED" not in {row_a.enrich_state, row_b.enrich_state}


def test_solid_lsp_unavailable_backoff_escalates_for_missing_server() -> None:
    """미설치/스폰 실패 계열은 3m -> 10m -> 30m cap 백오프를 적용해야 한다."""

    class _FakeHub:
        pass

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    now = time.monotonic()
    code = "ERR_LSP_SERVER_MISSING"
    for _ in range(2):
        backend._record_probe_state_from_extract_error(  # type: ignore[attr-defined]
            repo_root="/repo",
            relative_path="a.py",
            error_code=code,
            error_message="ERR_LSP_SERVER_MISSING: command not found",
        )
    state = backend._probe_state[("/repo", Language.PYTHON)]  # type: ignore[attr-defined]
    first_delta = state.next_retry_monotonic - now
    assert state.status == "UNAVAILABLE_COOLDOWN"
    assert 150.0 <= first_delta <= 210.0

    for _ in range(2):
        backend._record_probe_state_from_extract_error(  # type: ignore[attr-defined]
            repo_root="/repo",
            relative_path="a.py",
            error_code=code,
            error_message="ERR_LSP_SERVER_MISSING: command not found",
        )
    state = backend._probe_state[("/repo", Language.PYTHON)]  # type: ignore[attr-defined]
    second_delta = state.next_retry_monotonic - time.monotonic()
    assert 540.0 <= second_delta <= 660.0

    for _ in range(3):
        backend._record_probe_state_from_extract_error(  # type: ignore[attr-defined]
            repo_root="/repo",
            relative_path="a.py",
            error_code=code,
            error_message="ERR_LSP_SERVER_MISSING: command not found",
        )
    state = backend._probe_state[("/repo", Language.PYTHON)]  # type: ignore[attr-defined]
    third_delta = state.next_retry_monotonic - time.monotonic()
    assert 1700.0 <= third_delta <= 1900.0


def test_solid_lsp_workspace_mismatch_skips_until_manual_reset() -> None:
    """workspace mismatch는 즉시 skip 상태가 되고 수동 reset으로만 해제되어야 한다."""

    class _FakeHub:
        pass

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    backend._record_probe_state_from_extract_error(  # type: ignore[attr-defined]
        repo_root="/repo",
        relative_path="a.elm",
        error_code="ERR_LSP_WORKSPACE_MISMATCH",
        error_message="ERR_LSP_WORKSPACE_MISMATCH: No Elm workspace contains /repo/a.elm",
    )
    assert backend.is_l3_permanently_unavailable_for_file("/repo", "a.elm") is True
    state = backend._probe_state[("/repo", Language.ELM)]  # type: ignore[attr-defined]
    assert state.status == "WORKSPACE_MISMATCH"
    assert math.isinf(state.next_retry_monotonic)

    cleared = backend.clear_unavailable_state(repo_root="/repo", language="elm")
    assert cleared == 1
    assert backend.is_l3_permanently_unavailable_for_file("/repo", "a.elm") is False


def test_file_collection_scan_once_marks_candidate_index_dirty(tmp_path: Path) -> None:
    """scan_once에서 변경이 발생하면 후보 인덱스 dirty를 호출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-b"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    dirty_sink = _DirtySink()

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        candidate_index_sink=dirty_sink,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert dirty_sink.upsert_calls == 1


def test_file_collection_scan_once_configures_top_hot_languages(tmp_path: Path) -> None:
    """scan 결과 기준 상위 언어만 prewarm 대상으로 설정해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-hot"
    repo_dir.mkdir()

    for index in range(40):
        (repo_dir / f"py_{index}.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    for index in range(35):
        (repo_dir / f"kt_{index}.kt").write_text("fun alpha(): Int = 1\n", encoding="utf-8")
    for index in range(20):
        (repo_dir / f"go_{index}.go").write_text("package main\nfunc alpha() int { return 1 }\n", encoding="utf-8")

    backend = _CaptureHotLanguageBackend()
    policy = CollectionPolicyDTO(
        include_ext=(".py", ".kt", ".go"),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=policy,
        lsp_backend=backend,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert backend.repo_root == str(repo_dir.resolve())
    assert backend.languages == {Language.PYTHON, Language.KOTLIN}


def test_file_collection_rebalance_jobs_by_language_round_robin() -> None:
    """언어 버킷을 라운드로빈으로 교차 배치해야 한다."""
    _EXT_LANG = {".py": Language.PYTHON, ".kt": Language.KOTLIN}

    def _resolve(relative_path: str):
        ext = relative_path[relative_path.rfind("."):]
        return _EXT_LANG.get(ext)

    scheduling = L3SchedulingService(
        resolve_lsp_language=_resolve,
        lsp_backend=object(),
        l3_parallel_enabled=False,
        executor_max_workers=1,
        backpressure_on_interactive=False,
        backpressure_cooldown_sec=0.0,
        monotonic_now=lambda: 0.0,
    )

    jobs = [
        FileEnrichJobDTO(job_id="j1", repo_id="r_r", repo_root="/r", relative_path="a.py", content_hash="h1", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j2", repo_id="r_r", repo_root="/r", relative_path="b.py", content_hash="h2", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j3", repo_id="r_r", repo_root="/r", relative_path="c.kt", content_hash="h3", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j4", repo_id="r_r", repo_root="/r", relative_path="d.kt", content_hash="h4", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
    ]

    rebalanced = scheduling.rebalance_jobs_by_language(jobs)
    ordered_paths = [job.relative_path for job in rebalanced]

    assert ordered_paths == ["a.py", "c.kt", "b.py", "d.kt"]


def test_tantivy_applies_pending_change_without_full_sync(tmp_path: Path, monkeypatch) -> None:
    """pending change가 있으면 전체 sync 없이 인덱스에 반영되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-c"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_c",
            repo_root=str(repo_dir.resolve()),
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-change",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-c", indexed_at=None, is_active=True)
    sync_count = {"value": 0}
    original_sync_index = backend._sync_index

    def _tracked_sync(workspaces: list[WorkspaceDTO]) -> None:
        sync_count["value"] += 1
        original_sync_index(workspaces)

    monkeypatch.setattr(backend, "_sync_index", _tracked_sync)
    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert len(items) == 1
    assert sync_count["value"] == 0


def test_tantivy_applies_delete_change_and_removes_document(tmp_path: Path) -> None:
    """delete change 반영 실패가 감지되면 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-d"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_d",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-delete",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-d", indexed_at=None, is_active=True)
    indexed = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    assert len(indexed) == 1

    change_repo.enqueue_delete(
        repo_id="r_repo_d",
        repo_root=repo_root,
        relative_path="alpha.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )
    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate delete visibility check failed" in str(exc)


def test_tantivy_backend_does_not_use_tombstone_filter_field(tmp_path: Path) -> None:
    """Batch-22 이후 백엔드는 tombstone 필드에 의존하지 않아야 한다."""
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-no-tombstone",
    )
    assert not hasattr(backend, "_suppressed_paths")
    assert not hasattr(backend, "_deleted_paths_cache")
    assert hasattr(backend, "_writer")


def test_tantivy_pending_change_failure_raises_backend_error(tmp_path: Path) -> None:
    """pending 변경 적용 실패는 즉시 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-e"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    # mtime/size를 의도적으로 잘못 넣어 apply 실패를 유도한다.
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_e",
            repo_root=str(repo_dir.resolve()),
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=1,
            size_bytes=2,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-fail",
        change_repo=change_repo,
        max_maintenance_ms_per_search=5_000,
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-e", indexed_at=None, is_active=True)

    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate apply failed" in str(exc)


def test_tantivy_delete_visibility_failure_escalates_backend_error(tmp_path: Path, monkeypatch) -> None:
    """삭제 후 가시성 검증 실패는 즉시 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-f"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_f",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-delete-visibility-fail",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-f", indexed_at=None, is_active=True)
    _ = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    change_repo.enqueue_delete(
        repo_id="r_repo_f",
        repo_root=repo_root,
        relative_path="alpha.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )

    def _always_visible(repo_root: str, relative_path: str) -> bool:
        _ = (repo_root, relative_path)
        return True

    monkeypatch.setattr(backend, "_is_repo_path_visible", _always_visible)

    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate delete visibility check failed" in str(exc)


def test_tantivy_apply_allows_repo_under_active_workspace(tmp_path: Path) -> None:
    """active workspace 하위 repo는 candidate apply에서 비활성으로 오판하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = tmp_path / "workspace"
    repo_dir = workspace_root / "apps" / "repo-g"
    repo_dir.mkdir(parents=True)
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()

    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_g",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-workspace-child",
        change_repo=change_repo,
        max_maintenance_ms_per_search=5_000,
    )
    workspace = WorkspaceDTO(path=str(workspace_root.resolve()), name="workspace", indexed_at=None, is_active=True)
    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert len(items) == 1
    assert items[0].relative_path == "alpha.py"


def test_tantivy_pending_apply_respects_batch_cap(tmp_path: Path) -> None:
    """검색 1회당 pending apply는 설정된 배치 상한을 넘기면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-batch-cap"
    repo_dir.mkdir()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    workspace = WorkspaceDTO(path=repo_root, name="repo-batch-cap", indexed_at=None, is_active=True)

    for index in range(15):
        target = repo_dir / f"alpha_{index}.py"
        target.write_text(f"def alpha_symbol_{index}():\n    return {index}\n", encoding="utf-8")
        raw = target.read_bytes()
        change_repo.enqueue_upsert(
            CandidateIndexChangeDTO(
                repo_id="r_repo_batch_cap",
                repo_root=repo_root,
                relative_path=f"alpha_{index}.py",
                absolute_path=str(target.resolve()),
                content_hash=hashlib.sha256(raw).hexdigest(),
                mtime_ns=target.stat().st_mtime_ns,
                size_bytes=target.stat().st_size,
                event_source="scan",
                recorded_at="2026-02-19T00:00:00+00:00",
            )
        )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-batch-cap",
        change_repo=change_repo,
        max_pending_apply_per_search=5,
        max_maintenance_ms_per_search=1000,
    )

    first_items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=50)
    assert len(first_items) == 5
    assert len(change_repo.acquire_pending(limit=100)) == 10

    second_items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=50)
    assert len(second_items) == 10


def test_tantivy_pending_apply_respects_zero_time_budget(tmp_path: Path) -> None:
    """maintenance 시간 예산이 0이면 pending apply는 이월되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-budget-zero"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_budget_zero",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-19T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-budget-zero",
        change_repo=change_repo,
        max_pending_apply_per_search=10,
        max_maintenance_ms_per_search=0,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-budget-zero", indexed_at=None, is_active=True)

    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    assert len(items) == 0
    assert len(change_repo.acquire_pending(limit=10)) == 1


def test_tantivy_pending_apply_backpressure_reduces_batch_limit(tmp_path: Path, monkeypatch) -> None:
    """pending 큐 압력이 높으면 적용 배치 상한이 자동 축소되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-backpressure"
    repo_dir.mkdir()
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-backpressure", indexed_at=None, is_active=True)
    change_repo = CandidateIndexChangeRepository(db_path)
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-backpressure",
        change_repo=change_repo,
        max_pending_apply_per_search=60,
        min_pending_apply_on_pressure=24,
        max_maintenance_ms_per_search=1000,
    )

    captured_limit = {"value": 0}

    def _high_pressure_count() -> int:
        return 6000

    def _capture_acquire_pending(limit: int):  # type: ignore[no-untyped-def]
        captured_limit["value"] = limit
        return []

    monkeypatch.setattr(change_repo, "count_pending_changes", _high_pressure_count)
    monkeypatch.setattr(change_repo, "acquire_pending", _capture_acquire_pending)

    _ = backend.search(workspaces=[workspace], query="alpha", limit=10)

    assert captured_limit["value"] == 24


def test_tantivy_pending_apply_does_not_fail_on_other_registered_repo_changes(tmp_path: Path) -> None:
    """검색 대상 workspace 외 repo 변경이 pending에 있어도 검색이 실패하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    repo_a = workspace_root / "repo-a"
    repo_b = workspace_root / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    file_a = repo_a / "alpha.py"
    file_b = repo_b / "beta.py"
    file_a.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    file_b.write_text("def beta_symbol():\n    return 2\n", encoding="utf-8")

    change_repo = CandidateIndexChangeRepository(db_path)
    raw_a = file_a.read_bytes()
    raw_b = file_b.read_bytes()
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_a",
            repo_root=str(repo_a.resolve()),
            relative_path="alpha.py",
            absolute_path=str(file_a.resolve()),
            content_hash=hashlib.sha256(raw_a).hexdigest(),
            mtime_ns=file_a.stat().st_mtime_ns,
            size_bytes=file_a.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-19T00:00:00+00:00",
        )
    )
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_b",
            repo_root=str(repo_b.resolve()),
            relative_path="beta.py",
            absolute_path=str(file_b.resolve()),
            content_hash=hashlib.sha256(raw_b).hexdigest(),
            mtime_ns=file_b.stat().st_mtime_ns,
            size_bytes=file_b.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-19T00:00:01+00:00",
        )
    )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024, allowed_suffixes=(".py",)),
        index_root=tmp_path / "candidate-index-cross-repo-pending",
        change_repo=change_repo,
    )

    workspace = WorkspaceDTO(path=str(repo_a.resolve()), name="repo-a", indexed_at=None, is_active=True)
    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert any(item.relative_path == "alpha.py" for item in items)


def test_solid_lsp_scope_override_cache_record_get_and_invalidate() -> None:
    """PR-B baseline scope learning 캐시는 TTL 조회/경로 무효화를 지원해야 한다."""

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.TYPESCRIPT

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            raise AssertionError("not used")

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    repo_root = "/workspace"
    rel = "repo_a/src/app/main.ts"
    assert backend.get_scope_override(repo_root=repo_root, relative_path=rel) is None

    backend.record_scope_override_success(
        repo_root=repo_root,
        relative_path=rel,
        scope_root="/workspace/repo_a",
        scope_level="repo",
    )
    learned = backend.get_scope_override(repo_root=repo_root, relative_path=rel)
    assert learned == ("/workspace/repo_a", "repo")

    removed = backend.invalidate_scope_override_path(repo_root=repo_root, relative_path="repo_a/src")
    assert removed >= 1
    assert backend.get_scope_override(repo_root=repo_root, relative_path=rel) is None


def test_solid_lsp_scope_override_cache_ttl_expiry() -> None:
    """scope learning 캐시는 TTL 만료 시 조회되지 않아야 한다."""

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            raise AssertionError("not used")

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    backend._scope_override_ttl_sec = 0.001  # type: ignore[attr-defined]
    backend.record_scope_override_success(
        repo_root="/workspace",
        relative_path="repo_a/a.py",
        scope_root="/workspace/repo_a",
        scope_level="repo",
    )
    time.sleep(0.01)
    assert backend.get_scope_override(repo_root="/workspace", relative_path="repo_a/a.py") is None


def test_solid_lsp_backend_scope_planner_uses_planned_runtime_root() -> None:
    """planner가 계산한 scope root를 hub runtime root로 사용해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            del relative_path
            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.last_repo_root: str | None = None

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.last_repo_root = repo_root
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    class _FakePlanner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            self.calls.append((workspace_repo_root, relative_path, language.value))

            class _Result:
                lsp_scope_root = "/workspace/repo-a/module-x"
                strategy = "marker"
                marker_file = "pom.xml"

            return _Result()

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    planner = _FakePlanner()
    backend.configure_lsp_scope_planner(planner=planner, enabled=True)

    _ = backend.extract(repo_root="/workspace/repo-a", relative_path="module-x/src/App.java", content_hash="h1")

    assert planner.calls == [("/workspace/repo-a", "module-x/src/App.java", "java")]
    assert hub.last_repo_root == "/workspace/repo-a/module-x"


def test_solid_lsp_backend_scope_planner_active_mode_uses_planned_root() -> None:
    """shadow 모드가 아니면 planner가 계산한 scope root를 hub에 전달해야 한다."""

    class _FakeLsp:
        def __init__(self) -> None:
            self.last_relative_path: str | None = None

        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            self.last_relative_path = relative_path
            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])
            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.last_repo_root: str | None = None
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.last_repo_root = repo_root
            return self.lsp

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    class _FakePlanner:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del workspace_repo_root, relative_path, language

            class _Result:
                lsp_scope_root = "/workspace/repo-a/module-x"
                strategy = "marker"
                marker_file = "pom.xml"

            return _Result()

        def to_scope_relative_path(self, *, workspace_relative_path: str, scope_candidate_root: str) -> str:
            del scope_candidate_root
            return workspace_relative_path.removeprefix("module-x/")

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_lsp_scope_planner(planner=_FakePlanner(), enabled=True)

    _ = backend.extract(repo_root="/workspace/repo-a", relative_path="module-x/src/App.java", content_hash="h1")

    assert hub.last_repo_root == "/workspace/repo-a/module-x"
    assert hub.lsp.last_relative_path == "src/App.java"


def test_solid_lsp_backend_scope_override_is_applied_before_planner() -> None:
    """성공 scope override 캐시가 존재하면 planner보다 우선 적용되어야 한다."""

    class _FakeLsp:
        def __init__(self) -> None:
            self.last_relative_path: str | None = None

        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            self.last_relative_path = relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.last_repo_root: str | None = None
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.last_repo_root = repo_root
            return self.lsp

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    class _PlannerShouldNotWin:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del workspace_repo_root, relative_path, language

            class _Result:
                lsp_scope_root = "/workspace/repo-a/WRONG"
                strategy = "marker"
                marker_file = "pom.xml"

            return _Result()

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_lsp_scope_planner(planner=_PlannerShouldNotWin(), enabled=True)
    backend.record_scope_override_success(
        repo_root="/workspace/repo-a",
        relative_path="module-x/src/App.java",
        scope_root="/workspace/repo-a/module-x",
        scope_level="module",
    )

    _ = backend.extract(repo_root="/workspace/repo-a", relative_path="module-x/src/App.java", content_hash="h1")

    assert hub.last_repo_root == "/workspace/repo-a/module-x"
    assert hub.lsp.last_relative_path == "src/App.java"
    assert backend.get_runtime_metrics().get("scope_override_hit_count") == 1


def test_solid_lsp_backend_scope_planner_counts_fallback_index_building() -> None:
    """planner가 FALLBACK_INDEX_BUILDING 전략을 반환하면 런타임 메트릭 카운터에 반영해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    class _FakePlanner:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del workspace_repo_root, relative_path, language

            class _Result:
                lsp_scope_root = "/workspace/repo-a"
                strategy = "FALLBACK_INDEX_BUILDING"
                marker_file = None

            return _Result()

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    backend.configure_lsp_scope_planner(planner=_FakePlanner(), enabled=True)

    _ = backend.extract(repo_root="/workspace/repo-a", relative_path="src/App.java", content_hash="h1")

    metrics = backend.get_runtime_metrics()
    assert metrics["scope_planner_fallback_index_building_count"] == 1


def test_solid_lsp_backend_broker_lease_guard_rejects_profiled_get_or_start() -> None:
    """PR3 baseline: profiled 언어는 broker lease 없이 hub.get_or_start 호출되면 안 된다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.get_or_start_calls = 0

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            self.get_or_start_calls += 1
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    broker = LspSessionBroker(
        profiles={
            "java": LspBrokerLanguageProfile(
                language="java",
                hot_lanes=0,
                backlog_lanes=0,
                sticky_idle_ttl_sec=10.0,
                switch_cooldown_sec=0.0,
                min_lease_ms=0,
            )
        },
        max_standby_sessions_per_lang=1,
        max_standby_sessions_per_budget_group=1,
        now_monotonic=time.monotonic,
    )
    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_session_runtime(
        session_broker=broker,
        watcher_hotness_tracker=WatcherHotnessTracker(now_monotonic=time.monotonic),
        enabled=True,
    )

    result = backend.extract(repo_root="/workspace/repo-a", relative_path="src/App.java", content_hash="h1")
    assert result.error_message is not None
    assert "ERR_LSP_BROKER_LEASE_REQUIRED" in result.error_message
    assert hub.get_or_start_calls == 0
    assert backend.get_runtime_metrics().get("broker_guard_reject_count") == 1


def test_solid_lsp_backend_broker_guard_bypasses_unprofiled_language() -> None:
    """PR3 baseline: 비프로파일 언어는 broker lease 거부 없이 hub로 직접 진행해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path
            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])
            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.get_or_start_calls = 0

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.MARKDOWN

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            self.get_or_start_calls += 1
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    # java만 프로파일링된 broker
    broker = LspSessionBroker(
        profiles={
            "java": LspBrokerLanguageProfile(
                language="java",
                hot_lanes=1,
                backlog_lanes=1,
                sticky_idle_ttl_sec=10.0,
                switch_cooldown_sec=0.0,
                min_lease_ms=0,
            )
        },
        max_standby_sessions_per_lang=1,
        max_standby_sessions_per_budget_group=1,
        now_monotonic=time.monotonic,
    )
    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_session_runtime(
        session_broker=broker,
        watcher_hotness_tracker=WatcherHotnessTracker(now_monotonic=time.monotonic),
        enabled=True,
    )

    result = backend.extract(repo_root="/workspace/repo-a", relative_path="README.md", content_hash="h1")
    assert result.error_message is None
    assert hub.get_or_start_calls == 1
    assert backend.get_runtime_metrics().get("broker_guard_reject_count", 0) == 0


def test_solid_lsp_backend_uses_group_pending_hints_for_broker_backlog_lane() -> None:
    """PR3.1 tuning: L3 그룹 힌트를 broker pending_jobs_in_scope 입력으로 사용해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            return self.lsp

        def get_metrics(self) -> dict[str, int]:
            return {}

    class _Lease:
        def __init__(self, pending: int, seen: list[int]) -> None:
            self.granted = True
            self.reason = "admitted"
            self._seen = seen
            self._pending = pending

        def __enter__(self):  # noqa: ANN001
            self._seen.append(self._pending)
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class _FakeBroker:
        def __init__(self) -> None:
            self.seen_pending: list[int] = []

        def is_profiled_language(self, language: Language) -> bool:
            return language == Language.JAVA

        def lease(
            self,
            *,
            language: Language,
            lsp_scope_root: str,
            lane: str,
            hotness_score: float,
            pending_jobs_in_scope: int,
            throughput_mode: bool = False,
        ):  # noqa: ANN001
            del language, lsp_scope_root, lane, hotness_score, throughput_mode
            return _Lease(pending_jobs_in_scope, self.seen_pending)

    @dataclass
    class _Job:
        repo_root: str
        relative_path: str

    hub = _FakeHub()
    broker = _FakeBroker()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_session_runtime(
        session_broker=broker,  # type: ignore[arg-type]
        watcher_hotness_tracker=WatcherHotnessTracker(now_monotonic=time.monotonic),
        enabled=True,
    )

    group = [
        _Job("/workspace/repo-a", "src/A.java"),
        _Job("/workspace/repo-a", "src/B.java"),
        _Job("/workspace/repo-a", "src/C.java"),
    ]
    backend.prime_l3_group_pending_hints(group_jobs=group)

    _ = backend.extract(repo_root="/workspace/repo-a", relative_path="src/A.java", content_hash="h1")

    assert broker.seen_pending, "broker lease should be called"
    assert broker.seen_pending[0] >= 2, "group pending hint should be greater than per-file default"


def test_solid_lsp_probe_worker_uses_scope_planner_runtime_root() -> None:
    """PR3 baseline: probe/prewarm 경로도 planner 계산 scope root를 사용해야 한다."""

    class _FakeLsp:
        def __init__(self) -> None:
            self.last_relative_path: str | None = None

        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            self.last_relative_path = relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.prewarm_roots: list[str] = []
            self.get_or_start_roots: list[str] = []
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language
            self.prewarm_roots.append(repo_root)

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.get_or_start_roots.append(repo_root)
            return self.lsp

        def acquire_l1_probe_slot(self):
            class _CM:
                def __enter__(self_inner):  # noqa: ANN001
                    return None

                def __exit__(self_inner, exc_type, exc, tb):  # noqa: ANN001
                    return False

            return _CM()

    class _FakePlanner:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del workspace_repo_root, relative_path, language

            class _Result:
                lsp_scope_root = "/workspace/repo-a/module-x"
                strategy = "marker"
                marker_file = "pom.xml"

            return _Result()

        def to_scope_relative_path(self, *, workspace_relative_path: str, scope_candidate_root: str) -> str:
            del scope_candidate_root
            return workspace_relative_path.removeprefix("module-x/")

    class _ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            class _DoneFuture:
                def result(self_inner, timeout=None):  # noqa: ANN001
                    del timeout
                    return None

            fn(*args, **kwargs)
            return _DoneFuture()

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend._l1_executor = _ImmediateExecutor()  # type: ignore[attr-defined]
    backend.configure_lsp_scope_planner(planner=_FakePlanner(), enabled=True)
    backend._probe_worker(("/workspace/repo-a", Language.JAVA), "module-x/src/App.java")  # type: ignore[attr-defined]

    assert hub.prewarm_roots == ["/workspace/repo-a/module-x"]
    assert hub.get_or_start_roots == ["/workspace/repo-a/module-x", "/workspace/repo-a/module-x"]
    assert hub.lsp.last_relative_path == "src/App.java"


def test_solid_lsp_probe_worker_runs_java_l1_probe() -> None:
    """배치 throughput 플래그 제거 이후 Java L1 probe(documentSymbol)는 수행되어야 한다."""

    class _FakeLsp:
        def __init__(self) -> None:
            self.last_relative_path: str | None = None

        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            self.last_relative_path = relative_path

            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])

            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.prewarm_roots: list[str] = []
            self.get_or_start_roots: list[str] = []
            self.lsp = _FakeLsp()

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language
            self.prewarm_roots.append(repo_root)

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.get_or_start_roots.append(repo_root)
            return self.lsp

        def acquire_l1_probe_slot(self):
            class _CM:
                def __enter__(self_inner):  # noqa: ANN001
                    return None

                def __exit__(self_inner, exc_type, exc, tb):  # noqa: ANN001
                    return False

            return _CM()

    class _FakePlanner:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del workspace_repo_root, relative_path, language

            class _Result:
                lsp_scope_root = "/workspace/repo-a/module-x"
                strategy = "marker"
                marker_file = "pom.xml"

            return _Result()

        def to_scope_relative_path(self, *, workspace_relative_path: str, scope_candidate_root: str) -> str:
            del scope_candidate_root
            return workspace_relative_path.removeprefix("module-x/")

    class _ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            class _DoneFuture:
                def result(self_inner, timeout=None):  # noqa: ANN001
                    del timeout
                    return None

            fn(*args, **kwargs)
            return _DoneFuture()

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend._l1_executor = _ImmediateExecutor()  # type: ignore[attr-defined]
    backend.configure_lsp_scope_planner(planner=_FakePlanner(), enabled=True)
    backend.configure_session_runtime(
        session_broker=None,
        watcher_hotness_tracker=None,
        enabled=False,
    )
    backend._probe_worker(("/workspace/repo-a", Language.JAVA), "module-x/src/App.java")  # type: ignore[attr-defined]

    assert hub.prewarm_roots == ["/workspace/repo-a/module-x"]
    assert hub.get_or_start_roots.count("/workspace/repo-a/module-x") >= 1
    assert hub.lsp.last_relative_path == "src/App.java"


def test_solid_lsp_backend_scope_planner_can_limit_active_languages() -> None:
    """PR3.3: scope planner 활성 언어 제한 시 비대상 언어는 repo_root 유지해야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path
            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])
            return _Req()

    class _FakeHub:
        def __init__(self) -> None:
            self.last_repo_root: str | None = None

        def resolve_language(self, relative_path: str) -> Language:
            if relative_path.endswith(".ts"):
                return Language.TYPESCRIPT
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, request_kind
            self.last_repo_root = repo_root
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    class _FakePlanner:
        def resolve(self, *, workspace_repo_root: str, relative_path: str, language: Language):  # noqa: ANN001
            del relative_path, language

            class _Result:
                lsp_scope_root = workspace_repo_root + "/ts-app"
                strategy = "marker"
                marker_file = "tsconfig.json"

            return _Result()

    hub = _FakeHub()
    backend = SolidLspExtractionBackend(hub=hub)  # type: ignore[arg-type]
    backend.configure_lsp_scope_planner(planner=_FakePlanner(), enabled=True)
    backend.configure_scope_runtime_policy(active_languages=("java",))  # typescript는 제외

    result = backend.extract(repo_root="/workspace/repo-a", relative_path="web/main.ts", content_hash="h1")
    assert result.error_message is None
    assert hub.last_repo_root == "/workspace/repo-a", "unlisted language should bypass planner application"


def test_solid_lsp_backend_broker_throughput_mode_flag_is_disabled() -> None:
    """batch throughput 모드를 제거했으므로 broker lease throughput_mode는 항상 False여야 한다."""

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str):  # noqa: ANN001
            del relative_path
            class _Req:
                def iter_symbols(self_inner):  # noqa: ANN001
                    del self_inner
                    return iter([])
            return _Req()

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.JAVA

        def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing"):  # noqa: ANN001
            del language, repo_root, request_kind
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

        def get_metrics(self) -> dict[str, int]:
            return {}

    class _Lease:
        def __init__(self, seen: list[bool], throughput_mode: bool) -> None:
            self.granted = True
            self.reason = "admitted"
            self._seen = seen
            self._mode = throughput_mode

        def __enter__(self):  # noqa: ANN001
            self._seen.append(self._mode)
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class _FakeBroker:
        def __init__(self) -> None:
            self.seen_modes: list[bool] = []

        def is_profiled_language(self, language: Language) -> bool:
            return language == Language.JAVA

        def lease(self, *, language: Language, lsp_scope_root: str, lane: str, hotness_score: float, pending_jobs_in_scope: int, throughput_mode: bool = False):  # noqa: ANN001
            del language, lsp_scope_root, lane, hotness_score, pending_jobs_in_scope
            return _Lease(self.seen_modes, throughput_mode)

    @dataclass
    class _Job:
        repo_root: str
        relative_path: str

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    broker = _FakeBroker()
    backend.configure_session_runtime(
        session_broker=broker,  # type: ignore[arg-type]
        watcher_hotness_tracker=WatcherHotnessTracker(now_monotonic=time.monotonic),
        enabled=True,
    )

    backend.prime_l3_group_pending_hints(group_jobs=[_Job("/workspace/repo", "A.java")])
    _ = backend.extract(repo_root="/workspace/repo", relative_path="A.java", content_hash="h1")
    backend.prime_l3_group_pending_hints(group_jobs=[
        _Job("/workspace/repo", "B.java"),
        _Job("/workspace/repo", "C.java"),
        _Job("/workspace/repo", "D.java"),
        _Job("/workspace/repo", "E.java"),
    ])
    _ = backend.extract(repo_root="/workspace/repo", relative_path="B.java", content_hash="h2")

    assert broker.seen_modes[:2] == [False, False]
