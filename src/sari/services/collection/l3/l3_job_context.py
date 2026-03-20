"""Shared mutable context for L3 orchestration stages."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import (
    EnrichStateUpdateDTO,
    FileBodyDeleteTargetDTO,
    FileEnrichFailureUpdateDTO,
    LspExtractPersistDTO,
    ToolReadinessStateDTO,
)


@dataclass
class L3JobContext:
    done_id: str | None = None
    failure_update: FileEnrichFailureUpdateDTO | None = None
    state_update: EnrichStateUpdateDTO | None = None
    body_delete: FileBodyDeleteTargetDTO | None = None
    lsp_update: LspExtractPersistDTO | None = None
    content_text: str | None = None
    readiness_update: ToolReadinessStateDTO | None = None
    l3_layer_upsert: dict[str, object] | None = None
    l4_layer_upsert: dict[str, object] | None = None
    l5_layer_upsert: dict[str, object] | None = None
