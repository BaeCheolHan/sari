"""Finalize stage for L3 orchestrator result composition."""

from __future__ import annotations

from datetime import timezone, datetime
from typing import Callable

from ..l3_job_context import L3JobContext
from sari.services.collection.enrich_result_dto import _LayerUpsertsDTO


class L3FinalizeStage:
    """Compose final job result and emit optional event."""

    def __init__(
        self,
        *,
        result_builder: Callable[..., object],
        event_repo: object | None,
    ) -> None:
        self._result_builder = result_builder
        self._event_repo = event_repo

    def execute(
        self,
        *,
        job_id: str,
        finished_status: str,
        elapsed_ms: float,
        context: L3JobContext,
        dev_error: object | None,
    ) -> object:
        if self._event_repo is not None:
            self._event_repo.record_event(
                job_id=job_id,
                status=finished_status,
                latency_ms=int(elapsed_ms),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        return self._result_builder(
            job_id=job_id,
            finished_status=finished_status,
            elapsed_ms=elapsed_ms,
            done_id=context.done_id,
            failure_update=context.failure_update,
            state_update=context.state_update,
            body_delete=context.body_delete,
            lsp_update=context.lsp_update,
            readiness_update=context.readiness_update,
            layer_upserts=_LayerUpsertsDTO(
                l3_layer_upsert=context.l3_layer_upsert,
                l4_layer_upsert=context.l4_layer_upsert,
                l5_layer_upsert=context.l5_layer_upsert,
            ),
            dev_error=dev_error,
        )
