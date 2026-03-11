"""L3 extract 성공 처리(stage)."""

from __future__ import annotations

from typing import Callable

from sari.core.models import FileEnrichJobDTO, L4AdmissionDecisionDTO, L5ReasonCode
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessResultDTO

from ..l3_job_context import L3JobContext


class L3ExtractSuccessStage:
    """extract 성공 시 shadow 기록 + L5 성공 persist를 담당한다."""

    _ZERO_RELATION_RETRY_MIN_RELATION_TARGET_SYMBOLS = 20

    def __init__(
        self,
        *,
        persist_stage: object,
        record_quality_shadow_compare: Callable[..., None],
        l5_queue_transition: object,
    ) -> None:
        self._persist_stage = persist_stage
        self._record_quality_shadow_compare = record_quality_shadow_compare
        self._l5_queue_transition = l5_queue_transition

    def handle_success(
        self,
        *,
        context: L3JobContext,
        job: FileEnrichJobDTO,
        language: str,
        preprocess_result: L3PreprocessResultDTO | None,
        admission_decision: L4AdmissionDecisionDTO | None,
        extraction: object,
        now_iso: str,
    ) -> str:
        symbols = list(getattr(extraction, "symbols", []))
        relations = list(getattr(extraction, "relations", []))
        self._record_quality_shadow_compare(
            job=job,
            language=language,
            preprocess_result=preprocess_result,
            lsp_symbols=symbols,
        )
        reason_code = (
            admission_decision.reason_code
            if admission_decision is not None and admission_decision.reason_code is not None
            else L5ReasonCode.GOLDENSET_COVERAGE
        )
        defer_zero_relations = getattr(self._l5_queue_transition, "defer_after_zero_relations", None)
        retry_zero_relations = (
            len(relations) == 0
            and self._should_retry_zero_relations(symbols=symbols)
            and callable(defer_zero_relations)
            and bool(defer_zero_relations(job=job))
        )
        self._persist_stage.apply_l5_success(
            context=context,
            repo_id=job.repo_id,
            repo_root=job.repo_root,
            relative_path=job.relative_path,
            content_hash=job.content_hash,
            preprocess_result=preprocess_result,
            admission_decision=admission_decision,
            reason_code=reason_code,
            lsp_symbols=symbols,
            lsp_relations=relations,
            now_iso=now_iso,
            retry_zero_relations_pending=retry_zero_relations,
        )
        context.done_id = job.job_id
        if retry_zero_relations:
            return "DONE"
        return "DONE"

    def _should_retry_zero_relations(self, *, symbols: list[dict[str, object]]) -> bool:
        relation_target_count = 0
        for symbol in symbols:
            kind = str(symbol.get("kind", "")).strip().lower()
            if kind in {"function", "method", "constructor", "class", "12", "6", "9", "5"}:
                relation_target_count += 1
        return relation_target_count >= self._ZERO_RELATION_RETRY_MIN_RELATION_TARGET_SYMBOLS
