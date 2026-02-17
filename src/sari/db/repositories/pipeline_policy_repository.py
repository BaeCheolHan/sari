"""파이프라인 운영 정책 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import PipelinePolicyDTO, now_iso8601_utc
from sari.db.row_mapper import row_bool, row_int, row_str
from sari.db.schema import connect


class PipelinePolicyRepository:
    """운영 정책 SSOT를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def get_policy(self) -> PipelinePolicyDTO:
        """현재 운영 정책을 조회한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT deletion_hold, l3_p95_threshold_ms, dead_ratio_threshold_bps, enrich_worker_count,
                       bootstrap_mode_enabled, bootstrap_l3_worker_count, bootstrap_l3_queue_max,
                       bootstrap_exit_min_l2_coverage_bps, bootstrap_exit_max_sec,
                       alert_window_sec, updated_at
                FROM pipeline_policy
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_policy default row is missing")
        return PipelinePolicyDTO(
            deletion_hold=row_bool(row, "deletion_hold"),
            l3_p95_threshold_ms=row_int(row, "l3_p95_threshold_ms"),
            dead_ratio_threshold_bps=row_int(row, "dead_ratio_threshold_bps"),
            enrich_worker_count=row_int(row, "enrich_worker_count"),
            bootstrap_mode_enabled=row_bool(row, "bootstrap_mode_enabled"),
            bootstrap_l3_worker_count=row_int(row, "bootstrap_l3_worker_count"),
            bootstrap_l3_queue_max=row_int(row, "bootstrap_l3_queue_max"),
            bootstrap_exit_min_l2_coverage_bps=row_int(row, "bootstrap_exit_min_l2_coverage_bps"),
            bootstrap_exit_max_sec=row_int(row, "bootstrap_exit_max_sec"),
            updated_at=row_str(row, "updated_at"),
        )

    def get_alert_window_sec(self) -> int:
        """알람 계산 윈도우(초)를 반환한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT alert_window_sec
                FROM pipeline_policy
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            return 300
        return row_int(row, "alert_window_sec")

    def update_policy(
        self,
        deletion_hold: bool | None = None,
        l3_p95_threshold_ms: int | None = None,
        dead_ratio_threshold_bps: int | None = None,
        enrich_worker_count: int | None = None,
        bootstrap_mode_enabled: bool | None = None,
        bootstrap_l3_worker_count: int | None = None,
        bootstrap_l3_queue_max: int | None = None,
        bootstrap_exit_min_l2_coverage_bps: int | None = None,
        bootstrap_exit_max_sec: int | None = None,
        alert_window_sec: int | None = None,
    ) -> PipelinePolicyDTO:
        """운영 정책을 부분 업데이트한다."""
        current = self.get_policy()
        current_window = self.get_alert_window_sec()
        next_deletion_hold = current.deletion_hold if deletion_hold is None else deletion_hold
        next_l3_p95 = current.l3_p95_threshold_ms if l3_p95_threshold_ms is None else l3_p95_threshold_ms
        next_dead_ratio = current.dead_ratio_threshold_bps if dead_ratio_threshold_bps is None else dead_ratio_threshold_bps
        next_workers = current.enrich_worker_count if enrich_worker_count is None else enrich_worker_count
        next_bootstrap_enabled = current.bootstrap_mode_enabled if bootstrap_mode_enabled is None else bootstrap_mode_enabled
        next_bootstrap_l3_workers = (
            current.bootstrap_l3_worker_count if bootstrap_l3_worker_count is None else bootstrap_l3_worker_count
        )
        next_bootstrap_l3_queue_max = current.bootstrap_l3_queue_max if bootstrap_l3_queue_max is None else bootstrap_l3_queue_max
        next_bootstrap_exit_l2_bps = (
            current.bootstrap_exit_min_l2_coverage_bps
            if bootstrap_exit_min_l2_coverage_bps is None
            else bootstrap_exit_min_l2_coverage_bps
        )
        next_bootstrap_exit_max_sec = current.bootstrap_exit_max_sec if bootstrap_exit_max_sec is None else bootstrap_exit_max_sec
        next_window = current_window if alert_window_sec is None else alert_window_sec
        updated_at = now_iso8601_utc()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE pipeline_policy
                SET deletion_hold = :deletion_hold,
                    l3_p95_threshold_ms = :l3_p95_threshold_ms,
                    dead_ratio_threshold_bps = :dead_ratio_threshold_bps,
                    enrich_worker_count = :enrich_worker_count,
                    bootstrap_mode_enabled = :bootstrap_mode_enabled,
                    bootstrap_l3_worker_count = :bootstrap_l3_worker_count,
                    bootstrap_l3_queue_max = :bootstrap_l3_queue_max,
                    bootstrap_exit_min_l2_coverage_bps = :bootstrap_exit_min_l2_coverage_bps,
                    bootstrap_exit_max_sec = :bootstrap_exit_max_sec,
                    alert_window_sec = :alert_window_sec,
                    updated_at = :updated_at
                WHERE singleton_key = 'default'
                """,
                {
                    "deletion_hold": 1 if next_deletion_hold else 0,
                    "l3_p95_threshold_ms": next_l3_p95,
                    "dead_ratio_threshold_bps": next_dead_ratio,
                    "enrich_worker_count": next_workers,
                    "bootstrap_mode_enabled": 1 if next_bootstrap_enabled else 0,
                    "bootstrap_l3_worker_count": next_bootstrap_l3_workers,
                    "bootstrap_l3_queue_max": next_bootstrap_l3_queue_max,
                    "bootstrap_exit_min_l2_coverage_bps": next_bootstrap_exit_l2_bps,
                    "bootstrap_exit_max_sec": next_bootstrap_exit_max_sec,
                    "alert_window_sec": next_window,
                    "updated_at": updated_at,
                },
            )
            conn.commit()
        return PipelinePolicyDTO(
            deletion_hold=next_deletion_hold,
            l3_p95_threshold_ms=next_l3_p95,
            dead_ratio_threshold_bps=next_dead_ratio,
            enrich_worker_count=next_workers,
            bootstrap_mode_enabled=next_bootstrap_enabled,
            bootstrap_l3_worker_count=next_bootstrap_l3_workers,
            bootstrap_l3_queue_max=next_bootstrap_l3_queue_max,
            bootstrap_exit_min_l2_coverage_bps=next_bootstrap_exit_l2_bps,
            bootstrap_exit_max_sec=next_bootstrap_exit_max_sec,
            updated_at=updated_at,
        )

    def _ensure_default_row(self) -> None:
        """기본 정책 레코드를 보장한다."""
        now_iso = now_iso8601_utc()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_policy(
                    singleton_key, deletion_hold, l3_p95_threshold_ms, dead_ratio_threshold_bps,
                    enrich_worker_count, bootstrap_mode_enabled, bootstrap_l3_worker_count, bootstrap_l3_queue_max,
                    bootstrap_exit_min_l2_coverage_bps, bootstrap_exit_max_sec, alert_window_sec, updated_at
                )
                VALUES(
                    'default', 0, 180000, 10, 4, 0, 1, 1000, 9500, 1800, 300, :updated_at
                )
                ON CONFLICT(singleton_key) DO NOTHING
                """,
                {"updated_at": now_iso},
            )
            conn.commit()
