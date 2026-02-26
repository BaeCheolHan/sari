"""L3 flush 책임을 담당하는 coordinator."""

from __future__ import annotations

from typing import Callable

from sari.core.models import CollectedFileBodyDTO
from sari.services.collection.enrich_result_dto import _L3ResultBuffersDTO


class L3FlushCoordinator:
    """L3 누적 버퍼 flush를 담당한다."""

    def __init__(self, *, flush_enrich_buffers: Callable[..., None]) -> None:
        self._flush_enrich_buffers = flush_enrich_buffers

    def flush(self, *, buffers: _L3ResultBuffersDTO, body_upserts: list[CollectedFileBodyDTO]) -> None:
        self._flush_enrich_buffers(buffers=buffers, body_upserts=body_upserts)
