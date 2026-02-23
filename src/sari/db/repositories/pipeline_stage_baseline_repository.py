"""Stage exit baseline SSOT 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import now_iso8601_utc
from sari.db.row_mapper import row_int
from sari.db.schema import connect


class PipelineStageBaselineRepository:
    """Stage exit baseline 단일 상태를 영속 저장한다."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def get_l4_admission_rate_baseline_p50(self) -> float | None:
        """저장된 Stage A baseline(p50)을 조회한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT l4_admission_rate_baseline_p50
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_stage_baseline default row is missing")
        raw = row["l4_admission_rate_baseline_p50"]
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def get_l4_admission_rate_baseline_samples(self) -> int:
        """baseline 샘플 카운트를 반환한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT l4_admission_rate_baseline_samples
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_stage_baseline default row is missing")
        return row_int(row, "l4_admission_rate_baseline_samples")

    def get_p95_pending_available_age_baseline_sec(self) -> float | None:
        """저장된 Stage B pending age baseline(sec)을 조회한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT p95_pending_available_age_baseline_sec
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_stage_baseline default row is missing")
        raw = row["p95_pending_available_age_baseline_sec"]
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def get_p95_pending_available_age_baseline_samples(self) -> int:
        """pending age baseline 샘플 카운트를 반환한다."""
        self._ensure_default_row()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT p95_pending_available_age_baseline_samples
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("pipeline_stage_baseline default row is missing")
        return row_int(row, "p95_pending_available_age_baseline_samples")

    def initialize_l4_admission_rate_baseline(self, observed_p50: float) -> bool:
        """baseline이 비어있는 경우 관측값으로 1회 초기화한다.

        Returns:
            bool: baseline 신규 초기화 여부
        """
        self._ensure_default_row()
        normalized_value = float(max(0.0, observed_p50))
        now_iso = now_iso8601_utc()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT l4_admission_rate_baseline_p50, l4_admission_rate_baseline_samples
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("pipeline_stage_baseline default row is missing")
            current = row["l4_admission_rate_baseline_p50"]
            current_samples = int(row["l4_admission_rate_baseline_samples"] or 0)
            if current is not None:
                return False
            conn.execute(
                """
                UPDATE pipeline_stage_baseline
                SET l4_admission_rate_baseline_p50 = :baseline,
                    l4_admission_rate_baseline_samples = :samples,
                    updated_at = :updated_at
                WHERE singleton_key = 'default'
                """,
                {
                    "baseline": normalized_value,
                    "samples": max(1, current_samples + 1),
                    "updated_at": now_iso,
                },
            )
            conn.commit()
        return True

    def initialize_p95_pending_available_age_baseline(self, observed_sec: float) -> bool:
        """pending age baseline이 비어있는 경우 관측값으로 1회 초기화한다."""
        self._ensure_default_row()
        normalized_value = float(max(0.0, observed_sec))
        now_iso = now_iso8601_utc()
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT p95_pending_available_age_baseline_sec, p95_pending_available_age_baseline_samples
                FROM pipeline_stage_baseline
                WHERE singleton_key = 'default'
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("pipeline_stage_baseline default row is missing")
            current = row["p95_pending_available_age_baseline_sec"]
            current_samples = int(row["p95_pending_available_age_baseline_samples"] or 0)
            if current is not None:
                return False
            conn.execute(
                """
                UPDATE pipeline_stage_baseline
                SET p95_pending_available_age_baseline_sec = :baseline,
                    p95_pending_available_age_baseline_samples = :samples,
                    updated_at = :updated_at
                WHERE singleton_key = 'default'
                """,
                {
                    "baseline": normalized_value,
                    "samples": max(1, current_samples + 1),
                    "updated_at": now_iso,
                },
            )
            conn.commit()
        return True

    def _ensure_default_row(self) -> None:
        now_iso = now_iso8601_utc()
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO pipeline_stage_baseline(
                    singleton_key, l4_admission_rate_baseline_p50, l4_admission_rate_baseline_samples,
                    p95_pending_available_age_baseline_sec, p95_pending_available_age_baseline_samples, updated_at
                )
                VALUES('default', NULL, 0, NULL, 0, :updated_at)
                ON CONFLICT(singleton_key) DO NOTHING
                """,
                {"updated_at": now_iso},
            )
            conn.commit()
