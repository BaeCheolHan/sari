"""Persistence-oriented state composition stage for L3 orchestration."""

from __future__ import annotations
from typing import Callable

from sari.core.models import (
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    L4AdmissionDecisionDTO,
    L5ReasonCode,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
)

from ..l3_job_context import L3JobContext
from ..l3_treesitter_preprocess_service import L3PreprocessResultDTO


class L3PersistStage:
    """Compose L3/L4/L5 layer upserts and readiness/state payloads."""

    def __init__(
        self,
        *,
        layer_upsert_builder: object,
        deletion_hold_enabled: Callable[[], bool],
    ) -> None:
        self._layer_upsert_builder = layer_upsert_builder
        self._deletion_hold_enabled = deletion_hold_enabled

    def mark_recent_ready(
        self,
        *,
        context: L3JobContext,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        now_iso: str,
        reason: str = "skip_recent_success",
        get_callers_ready: bool = False,
    ) -> None:
        context.state_update = EnrichStateUpdateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            enrich_state="TOOL_READY",
            updated_at=now_iso,
        )
        context.readiness_update = ToolReadinessStateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=bool(get_callers_ready),
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason=reason,
            updated_at=now_iso,
        )
        if not self._deletion_hold_enabled():
            context.body_delete = FileBodyDeleteTargetDTO(
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
            )

    def mark_skipped(
        self,
        *,
        context: L3JobContext,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        now_iso: str,
        reason: str,
    ) -> None:
        context.state_update = EnrichStateUpdateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            enrich_state="L3_SKIPPED",
            updated_at=now_iso,
        )
        context.readiness_update = ToolReadinessStateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            list_files_ready=False,
            read_file_ready=False,
            search_symbol_ready=False,
            get_callers_ready=False,
            consistency_ready=False,
            quality_ready=False,
            tool_ready=False,
            last_reason=reason,
            updated_at=now_iso,
        )

    def apply_l3_only_success(
        self,
        *,
        context: L3JobContext,
        repo_id: str = "",
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO,
        admission_decision: L4AdmissionDecisionDTO | None,
        now_iso: str,
    ) -> None:
        context.l3_layer_upsert = self._layer_upsert_builder.build_l3(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            now_iso=now_iso,
        )
        context.l4_layer_upsert = self._layer_upsert_builder.build_l4(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            admission_decision=admission_decision,
            now_iso=now_iso,
        )
        context.lsp_update = LspExtractPersistDTO(
            repo_id=repo_id,
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            symbols=preprocess_result.symbols,
            relations=[],
            created_at=now_iso,
        )
        context.state_update = EnrichStateUpdateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            enrich_state="TOOL_READY",
            updated_at=now_iso,
        )
        context.readiness_update = ToolReadinessStateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            list_files_ready=True,
            read_file_ready=True,
            search_symbol_ready=True,
            get_callers_ready=False,
            consistency_ready=True,
            quality_ready=True,
            tool_ready=True,
            last_reason=preprocess_result.reason,
            updated_at=now_iso,
        )

    def apply_l5_success(
        self,
        *,
        context: L3JobContext,
        repo_id: str = "",
        repo_root: str,
        relative_path: str,
        content_hash: str,
        preprocess_result: L3PreprocessResultDTO | None,
        admission_decision: L4AdmissionDecisionDTO | None,
        reason_code: L5ReasonCode,
        lsp_symbols: list[dict[str, object]],
        lsp_relations: list[dict[str, object]],
        now_iso: str,
    ) -> None:
        context.l3_layer_upsert = self._layer_upsert_builder.build_l3(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            now_iso=now_iso,
        )
        context.l4_layer_upsert = self._layer_upsert_builder.build_l4(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            preprocess_result=preprocess_result,
            admission_decision=admission_decision,
            now_iso=now_iso,
        )
        context.l5_layer_upsert = self._layer_upsert_builder.build_l5(
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            reason_code=reason_code,
            symbols=lsp_symbols,
            relations=lsp_relations,
            now_iso=now_iso,
        )
        context.lsp_update = LspExtractPersistDTO(
            repo_id=repo_id,
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            symbols=lsp_symbols,
            relations=lsp_relations,
            created_at=now_iso,
        )
        self.mark_recent_ready(
            context=context,
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            now_iso=now_iso,
            reason="ok",
            get_callers_ready=len(lsp_relations) > 0,
        )

    def mark_failure(
        self,
        *,
        context: L3JobContext,
        job_id: str,
        repo_root: str,
        relative_path: str,
        now_iso: str,
        error_message: str,
        dead_threshold: int,
        backoff_base_sec: int,
    ) -> None:
        context.state_update = EnrichStateUpdateDTO(
            repo_root=repo_root,
            relative_path=relative_path,
            enrich_state="FAILED",
            updated_at=now_iso,
        )
        context.failure_update = FileEnrichFailureUpdateDTO(
            job_id=job_id,
            error_message=error_message,
            now_iso=now_iso,
            dead_threshold=dead_threshold,
            backoff_base_sec=backoff_base_sec,
        )
