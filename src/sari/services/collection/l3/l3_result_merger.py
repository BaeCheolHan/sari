"""L3 job result 병합 책임을 담당하는 merger."""

from __future__ import annotations

from sari.services.collection.enrich_result_dto import _L3JobResultDTO, _L3ResultBuffersDTO


class L3ResultMerger:
    """L3 결과를 누적 버퍼로 병합한다."""

    def merge(self, *, result: _L3JobResultDTO, buffers: _L3ResultBuffersDTO) -> None:
        buffers.merge_result(result)

