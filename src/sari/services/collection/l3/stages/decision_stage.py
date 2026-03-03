"""L3 skip/admission/preprocess 기반 실행 경로 결정(stage)."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import EnrichStateUpdateDTO, FileEnrichJobDTO, L4AdmissionDecisionDTO
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessDecision, L3PreprocessResultDTO

from ..l3_job_context import L3JobContext


@dataclass(frozen=True)
class L3DecisionStageResult:
    finished_status: str | None
    should_extract: bool
    now_iso: str
    language: str
    admission_decision: L4AdmissionDecisionDTO | None


class L3DecisionStage:
    """L3 처리에서 extract 실행 전 단계의 분기 판단을 담당한다."""

    def __init__(
        self,
        *,
        skip_eligibility: object,
        scope_resolution: object,
        admission_stage: object,
        queue_transition: object,
        l5_queue_transition: object,
        persist_stage: object,
        now_iso_supplier: object,
        admission_enforced: bool,
    ) -> None:
        self._skip_eligibility = skip_eligibility
        self._scope_resolution = scope_resolution
        self._admission_stage = admission_stage
        self._queue_transition = queue_transition
        self._l5_queue_transition = l5_queue_transition
        self._persist_stage = persist_stage
        self._now_iso_supplier = now_iso_supplier
        self._admission_enforced = bool(admission_enforced)

    def evaluate(
        self,
        *,
        context: L3JobContext,
        job: FileEnrichJobDTO,
        preprocess_result: L3PreprocessResultDTO | None,
        l5_lane: bool = False,
    ) -> L3DecisionStageResult:
        now_iso = self._now_iso_supplier()

        # Lane별 최근 성공 skip 체크
        if l5_lane:
            if bool(self._skip_eligibility.is_recent_l5_ready(job)):
                context.done_id = job.job_id
                return L3DecisionStageResult(
                    finished_status="DONE",
                    should_extract=False,
                    now_iso=now_iso,
                    language="",
                    admission_decision=None,
                )
        else:
            if bool(self._skip_eligibility.is_recent_tool_ready(job)):
                get_recent_state = getattr(self._skip_eligibility, "get_recent_tool_ready_state", None)
                recent_state = get_recent_state(job) if callable(get_recent_state) else None
                preserved_get_callers_ready = bool(getattr(recent_state, "get_callers_ready", False))
                self._persist_stage.mark_recent_ready(
                    context=context,
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    content_hash=job.content_hash,
                    now_iso=now_iso,
                    reason="skip_recent_success",
                    get_callers_ready=preserved_get_callers_ready,
                )
                context.done_id = job.job_id
                return L3DecisionStageResult(
                    finished_status="DONE",
                    should_extract=False,
                    now_iso=now_iso,
                    language="",
                    admission_decision=None,
                )

        skip_reason = self._skip_eligibility.resolve_skip_reason(job)
        if skip_reason is not None:
            context.state_update = EnrichStateUpdateDTO(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                enrich_state="L3_SKIPPED",
                updated_at=now_iso,
            )
            context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                job=job,
                reason=skip_reason,
                now_iso=now_iso,
            )
            context.done_id = job.job_id
            return L3DecisionStageResult(
                finished_status="DONE",
                should_extract=False,
                now_iso=now_iso,
                language="",
                admission_decision=None,
            )

        language = self._scope_resolution.resolve_language(job.relative_path)
        admission_decision = self._admission_stage.evaluate(job=job, language=language)

        if admission_decision is not None and not admission_decision.admit_l5 and self._admission_enforced:
            rejection = (
                admission_decision.reject_reason.value
                if admission_decision.reject_reason is not None
                else "unknown"
            )
            deferred = bool(
                self._l5_queue_transition.defer_after_l5_admission_rejection(
                    job=job,
                    admission=admission_decision,
                )
            )
            if deferred:
                return L3DecisionStageResult(
                    finished_status="PENDING",
                    should_extract=False,
                    now_iso=now_iso,
                    language=language,
                    admission_decision=admission_decision,
                )
            context.state_update = EnrichStateUpdateDTO(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                enrich_state="L3_SKIPPED",
                updated_at=now_iso,
            )
            context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                job=job,
                reason=f"l5_reject:{rejection}",
                now_iso=now_iso,
            )
            context.done_id = job.job_id
            return L3DecisionStageResult(
                finished_status="DONE",
                should_extract=False,
                now_iso=now_iso,
                language=language,
                admission_decision=admission_decision,
            )

        if preprocess_result is not None and preprocess_result.decision is L3PreprocessDecision.DEFERRED_HEAVY:
            deferred = bool(
                self._l5_queue_transition.defer_after_preprocess_heavy(
                    job=job,
                    reason=preprocess_result.reason,
                )
            )
            if deferred:
                return L3DecisionStageResult(
                    finished_status="PENDING",
                    should_extract=False,
                    now_iso=now_iso,
                    language=language,
                    admission_decision=admission_decision,
                )
            context.state_update = EnrichStateUpdateDTO(
                repo_root=job.repo_root,
                relative_path=job.relative_path,
                enrich_state="L3_SKIPPED",
                updated_at=now_iso,
            )
            context.readiness_update = self._skip_eligibility.build_skipped_readiness(
                job=job,
                reason=preprocess_result.reason,
                now_iso=now_iso,
            )
            context.done_id = job.job_id
            return L3DecisionStageResult(
                finished_status="DONE",
                should_extract=False,
                now_iso=now_iso,
                language=language,
                admission_decision=admission_decision,
            )

        # L3 lane: L3_ONLY/NEEDS_L5 구분 없이 즉시 L3 저장 후 DONE (모든 파일 즉각 응답)
        if not l5_lane:
            if preprocess_result is not None:
                self._persist_stage.apply_l3_only_success(
                    context=context,
                    repo_root=job.repo_root,
                    relative_path=job.relative_path,
                    content_hash=job.content_hash,
                    preprocess_result=preprocess_result,
                    admission_decision=admission_decision,
                    now_iso=now_iso,
                )
            context.done_id = job.job_id
            return L3DecisionStageResult(
                finished_status="DONE",
                should_extract=False,
                now_iso=now_iso,
                language=language,
                admission_decision=admission_decision,
            )

        # L5 lane: LSP 추출로 진행
        return L3DecisionStageResult(
            finished_status=None,
            should_extract=True,
            now_iso=now_iso,
            language=language,
            admission_decision=admission_decision,
        )
