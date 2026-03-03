"""L3 flush 책임을 담당하는 coordinator."""

from __future__ import annotations

from typing import Callable

from sari.core.event_bus import EventBus
from sari.core.events import L3FlushCompleted
from sari.core.models import CollectedFileBodyDTO
from sari.services.collection.enrich_result_dto import _L3ResultBuffersDTO


class L3FlushCoordinator:
    """L3 누적 버퍼 flush를 담당한다."""

    def __init__(
        self,
        *,
        flush_enrich_buffers: Callable[..., None],
        event_bus: EventBus | None = None,
    ) -> None:
        self._flush_enrich_buffers = flush_enrich_buffers
        self._event_bus = event_bus

    def flush(self, *, buffers: _L3ResultBuffersDTO, body_upserts: list[CollectedFileBodyDTO]) -> None:
        # flush 전에 repo_root와 flushed_count를 캡처한다 (flush 후 버퍼가 clear됨).
        flushed_count = len(buffers.done_ids)
        repo_root: str | None = None
        if buffers.state_updates:
            repo_root = buffers.state_updates[0].repo_root

        self._flush_enrich_buffers(buffers=buffers, body_upserts=body_upserts)

        # flush 완료 후 이벤트 발행 (repo_root를 알 수 있는 경우만)
        if self._event_bus is not None and flushed_count > 0 and repo_root is not None:
            try:
                self._event_bus.publish(
                    L3FlushCompleted(repo_root=repo_root, flushed_count=flushed_count),
                )
            except (RuntimeError, TypeError, ValueError, OSError):
                log.debug("L3FlushCompleted publish failed (repo=%s)", repo_root)
