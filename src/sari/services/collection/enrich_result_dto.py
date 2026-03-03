"""EnrichEngine 내부 결과 DTO/버퍼 정의."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from sari.core.exceptions import CollectionError
from sari.core.models import (
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
)
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository


@dataclass(frozen=True)
class _LayerUpsertsDTO:
    """L3/L4/L5 단계별 upsert payload를 묶어 표현한다."""

    l3_layer_upsert: dict[str, object] | None = None
    l4_layer_upsert: dict[str, object] | None = None
    l5_layer_upsert: dict[str, object] | None = None


@dataclass
class _LayerUpsertBucketsDTO:
    """L3/L4/L5 단계별 upsert 버퍼를 관리한다."""

    l3_layer_upserts: list[dict[str, object]] = field(default_factory=list)
    l4_layer_upserts: list[dict[str, object]] = field(default_factory=list)
    l5_layer_upserts: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "_LayerUpsertBucketsDTO":
        return cls()

    def merge_result(self, result: "_L3JobResultDTO") -> None:
        if result.l3_layer_upsert is not None:
            self.l3_layer_upserts.append(result.l3_layer_upsert)
        if result.l4_layer_upsert is not None:
            self.l4_layer_upserts.append(result.l4_layer_upsert)
        if result.l5_layer_upsert is not None:
            self.l5_layer_upserts.append(result.l5_layer_upsert)

    def flush(
        self,
        tool_layer_repo: ToolDataLayerRepository | None,
        *,
        conn: sqlite3.Connection | None = None,
        clear_after_flush: bool = True,
    ) -> None:
        if tool_layer_repo is None:
            if clear_after_flush:
                self.l3_layer_upserts.clear()
                self.l4_layer_upserts.clear()
                self.l5_layer_upserts.clear()
            return
        if self.l3_layer_upserts:
            if conn is None:
                tool_layer_repo.upsert_l3_symbols_many(self.l3_layer_upserts)
            else:
                tool_layer_repo.upsert_l3_symbols_many(self.l3_layer_upserts, conn=conn)
            if clear_after_flush:
                self.l3_layer_upserts.clear()
        if self.l4_layer_upserts:
            if conn is None:
                tool_layer_repo.upsert_l4_normalized_symbols_many(self.l4_layer_upserts)
            else:
                tool_layer_repo.upsert_l4_normalized_symbols_many(self.l4_layer_upserts, conn=conn)
            if clear_after_flush:
                self.l4_layer_upserts.clear()
        if self.l5_layer_upserts:
            if conn is None:
                tool_layer_repo.upsert_l5_semantics_many(self.l5_layer_upserts)
            else:
                tool_layer_repo.upsert_l5_semantics_many(self.l5_layer_upserts, conn=conn)
            if clear_after_flush:
                self.l5_layer_upserts.clear()


@dataclass
class _L3ResultBuffersDTO:
    """L3 처리 중 누적되는 flush 버퍼 묶음."""

    done_ids: list[str] = field(default_factory=list)
    failed_updates: list[FileEnrichFailureUpdateDTO] = field(default_factory=list)
    state_updates: list[EnrichStateUpdateDTO] = field(default_factory=list)
    body_deletes: list[FileBodyDeleteTargetDTO] = field(default_factory=list)
    lsp_updates: list[LspExtractPersistDTO] = field(default_factory=list)
    readiness_updates: list[ToolReadinessStateDTO] = field(default_factory=list)
    layer_upsert_buckets: _LayerUpsertBucketsDTO = field(default_factory=_LayerUpsertBucketsDTO.empty)

    @classmethod
    def empty(cls) -> "_L3ResultBuffersDTO":
        return cls()

    def merge_result(self, result: "_L3JobResultDTO") -> None:
        if result.done_id is not None:
            self.done_ids.append(result.done_id)
        if result.failure_update is not None:
            self.failed_updates.append(result.failure_update)
        if result.state_update is not None:
            self.state_updates.append(result.state_update)
        if result.body_delete is not None:
            self.body_deletes.append(result.body_delete)
        if result.lsp_update is not None:
            self.lsp_updates.append(result.lsp_update)
        if result.readiness_update is not None:
            self.readiness_updates.append(result.readiness_update)
        self.layer_upsert_buckets.merge_result(result)


@dataclass
class _L2ResultBuffersDTO:
    """L2 처리 중 누적되는 flush 버퍼 묶음."""

    done_ids: list[str] = field(default_factory=list)
    failed_updates: list[FileEnrichFailureUpdateDTO] = field(default_factory=list)
    state_updates: list[EnrichStateUpdateDTO] = field(default_factory=list)
    body_deletes: list[FileBodyDeleteTargetDTO] = field(default_factory=list)
    lsp_updates: list[LspExtractPersistDTO] = field(default_factory=list)
    readiness_updates: list[ToolReadinessStateDTO] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "_L2ResultBuffersDTO":
        return cls()


@dataclass(frozen=True)
class _L3JobResultDTO:
    """L3 작업 결과를 표현한다."""

    job_id: str
    finished_status: str
    elapsed_ms: float
    done_id: str | None
    failure_update: FileEnrichFailureUpdateDTO | None
    state_update: EnrichStateUpdateDTO | None
    body_delete: FileBodyDeleteTargetDTO | None
    lsp_update: LspExtractPersistDTO | None
    readiness_update: ToolReadinessStateDTO | None
    layer_upserts: _LayerUpsertsDTO = field(default_factory=_LayerUpsertsDTO)
    dev_error: CollectionError | None = None

    @property
    def l3_layer_upsert(self) -> dict[str, object] | None:
        return self.layer_upserts.l3_layer_upsert

    @property
    def l4_layer_upsert(self) -> dict[str, object] | None:
        return self.layer_upserts.l4_layer_upsert

    @property
    def l5_layer_upsert(self) -> dict[str, object] | None:
        return self.layer_upserts.l5_layer_upsert
