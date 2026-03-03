"""L2/L3 공통 flush coordinator."""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
import sys

from sari.core.models import CollectedFileBodyDTO
from sari.db.schema import connect
from sari.services.collection.enrich_result_dto import _L2ResultBuffersDTO, _L3ResultBuffersDTO

log = logging.getLogger(__name__)

# flush()가 받을 수 있는 버퍼 DTO 유니온 타입
_FlushBuffers = _L2ResultBuffersDTO | _L3ResultBuffersDTO


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

    def _resolve_db_path(self) -> Path | None:
        db_path = getattr(self._file_repo, "db_path", None)
        if isinstance(db_path, Path):
            return db_path
        fallback = getattr(self._file_repo, "_db_path", None)
        if isinstance(fallback, Path):
            return fallback
        return None

    def _has_work(self, buffers: _FlushBuffers, body_upserts: list[CollectedFileBodyDTO]) -> bool:
        """flush할 내용이 하나라도 있으면 True를 반환한다."""
        if body_upserts:
            return True
        if buffers.done_ids or buffers.failed_updates or buffers.state_updates:
            return True
        if buffers.body_deletes or buffers.lsp_updates or buffers.readiness_updates:
            return True
        if isinstance(buffers, _L3ResultBuffersDTO):
            buckets = buffers.layer_upsert_buckets
            if buckets.l3_layer_upserts or buckets.l4_layer_upserts or buckets.l5_layer_upserts:
                return True
        return False

    def flush(
        self,
        *,
        buffers: _FlushBuffers,
        body_upserts: list[CollectedFileBodyDTO],
    ) -> None:
        if not self._has_work(buffers, body_upserts):
            return
        layer_buckets = buffers.layer_upsert_buckets if isinstance(buffers, _L3ResultBuffersDTO) else None
        db_path = self._resolve_db_path()
        if db_path is None:
            # 테스트 더블/duck-typed repo 호환 경로: 단일 트랜잭션은 생략하고 기존 시그니처로 수행한다.
            if body_upserts:
                self._body_repo.upsert_body_many(body_upserts)
            if buffers.lsp_updates:
                self._lsp_repo.replace_file_data_many(buffers.lsp_updates)
            if buffers.readiness_updates:
                self._readiness_repo.upsert_state_many(buffers.readiness_updates)
            if layer_buckets is not None:
                layer_buckets.flush(tool_layer_repo=self._tool_layer_repo, clear_after_flush=False)
            if buffers.body_deletes:
                self._body_repo.delete_body_many(buffers.body_deletes)
            if buffers.state_updates:
                self._file_repo.update_enrich_state_many(buffers.state_updates)
            if buffers.done_ids:
                self._enrich_queue_repo.mark_done_many(buffers.done_ids)
            if buffers.failed_updates:
                self._enrich_queue_repo.mark_failed_with_backoff_many(buffers.failed_updates)
        else:
            conn = connect(db_path)
            committed = False
            try:
                if body_upserts:
                    self._body_repo.upsert_body_many(body_upserts, conn=conn)
                if buffers.lsp_updates:
                    self._lsp_repo.replace_file_data_many(buffers.lsp_updates, conn=conn)
                if buffers.readiness_updates:
                    self._readiness_repo.upsert_state_many(buffers.readiness_updates, conn=conn)
                if layer_buckets is not None:
                    layer_buckets.flush(tool_layer_repo=self._tool_layer_repo, conn=conn, clear_after_flush=False)
                if buffers.body_deletes:
                    self._body_repo.delete_body_many(buffers.body_deletes, conn=conn)
                if buffers.state_updates:
                    self._file_repo.update_enrich_state_many(buffers.state_updates, conn=conn)
                if buffers.done_ids:
                    self._enrich_queue_repo.mark_done_many(buffers.done_ids, conn=conn)
                if buffers.failed_updates:
                    self._enrich_queue_repo.mark_failed_with_backoff_many(buffers.failed_updates, conn=conn)
                conn.commit()
                committed = True
            finally:
                if not committed:
                    try:
                        if bool(getattr(conn, "in_transaction", True)):
                            conn.rollback()
                    except sqlite3.Error:
                        if sys.exc_info()[0] is None:
                            raise
                        log.warning("flush rollback failed while handling a prior exception", exc_info=True)
                try:
                    conn.close()
                except sqlite3.Error:
                    if sys.exc_info()[0] is None:
                        raise
                    log.warning("flush connection close failed while handling a prior exception", exc_info=True)

        body_upserts.clear()
        buffers.lsp_updates.clear()
        buffers.readiness_updates.clear()
        if layer_buckets is not None:
            layer_buckets.l3_layer_upserts.clear()
            layer_buckets.l4_layer_upserts.clear()
            layer_buckets.l5_layer_upserts.clear()
        buffers.body_deletes.clear()
        buffers.state_updates.clear()
        buffers.done_ids.clear()
        buffers.failed_updates.clear()
