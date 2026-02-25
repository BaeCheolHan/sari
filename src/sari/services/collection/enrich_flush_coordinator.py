"""L2/L3 공통 flush coordinator."""

from __future__ import annotations

from sari.core.models import (
    CollectedFileBodyDTO,
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
)
from sari.services.collection.enrich_result_dto import _LayerUpsertBucketsDTO


class EnrichFlushCoordinator:
    """공통 enrich flush(write-back) 절차를 담당한다."""

    def __init__(
        self,
        *,
        body_repo: object,
        lsp_repo: object,
        readiness_repo: object,
        file_repo: object,
        enrich_queue_repo: object,
        tool_layer_repo: object | None,
    ) -> None:
        self._body_repo = body_repo
        self._lsp_repo = lsp_repo
        self._readiness_repo = readiness_repo
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._tool_layer_repo = tool_layer_repo

    def flush(
        self,
        *,
        done_ids: list[str],
        failed_updates: list[FileEnrichFailureUpdateDTO],
        state_updates: list[EnrichStateUpdateDTO],
        body_upserts: list[CollectedFileBodyDTO],
        body_deletes: list[FileBodyDeleteTargetDTO],
        lsp_updates: list[LspExtractPersistDTO],
        readiness_updates: list[ToolReadinessStateDTO],
        l3_layer_upserts: list[dict[str, object]],
        l4_layer_upserts: list[dict[str, object]],
        l5_layer_upserts: list[dict[str, object]],
    ) -> None:
        if len(body_upserts) > 0:
            self._body_repo.upsert_body_many(body_upserts)
            body_upserts.clear()
        if len(lsp_updates) > 0:
            self._lsp_repo.replace_file_data_many(lsp_updates)
            lsp_updates.clear()
        if len(readiness_updates) > 0:
            self._readiness_repo.upsert_state_many(readiness_updates)
            readiness_updates.clear()
        _LayerUpsertBucketsDTO(
            l3_layer_upserts=l3_layer_upserts,
            l4_layer_upserts=l4_layer_upserts,
            l5_layer_upserts=l5_layer_upserts,
        ).flush(tool_layer_repo=self._tool_layer_repo)
        if len(body_deletes) > 0:
            self._body_repo.delete_body_many(body_deletes)
            body_deletes.clear()
        if len(state_updates) > 0:
            self._file_repo.update_enrich_state_many(state_updates)
            state_updates.clear()
        if len(done_ids) > 0:
            self._enrich_queue_repo.mark_done_many(done_ids)
            done_ids.clear()
        if len(failed_updates) > 0:
            self._enrich_queue_repo.mark_failed_with_backoff_many(failed_updates)
            failed_updates.clear()

