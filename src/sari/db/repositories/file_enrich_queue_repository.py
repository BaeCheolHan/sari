"""L2 본문 보강 큐 저장소를 구현한다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sari.core.models import DeadJobItemDTO, EnqueueRequestDTO, FileEnrichFailureUpdateDTO, FileEnrichJobDTO
from sari.db.row_mapper import row_int, row_optional_str, row_str
from sari.db.schema import connect


class FileEnrichQueueRepository:
    """L2 본문 보강 큐 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def enqueue(
        self,
        repo_root: str,
        relative_path: str,
        content_hash: str,
        priority: int,
        enqueue_source: str,
        now_iso: str,
        repo_id: str | None = None,
    ) -> str:
        """L2 본문 보강 큐 작업을 적재한다."""
        resolved_repo_id = repo_id if repo_id is not None and repo_id.strip() != "" else f"r_{uuid.uuid5(uuid.NAMESPACE_URL, repo_root).hex[:20]}"
        job_id = str(uuid.uuid4())
        job = FileEnrichJobDTO(
            job_id=job_id,
            repo_id=resolved_repo_id,
            repo_root=repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            priority=priority,
            enqueue_source=enqueue_source,
            status="PENDING",
            attempt_count=0,
            last_error=None,
            next_retry_at=now_iso,
            created_at=now_iso,
            updated_at=now_iso,
        )
        with connect(self._db_path) as conn:
            existing = conn.execute(
                """
                SELECT job_id, priority
                FROM file_enrich_queue
                WHERE repo_id = :repo_id
                  AND repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND status IN ('PENDING', 'FAILED')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                {"repo_id": resolved_repo_id, "repo_root": repo_root, "relative_path": relative_path},
            ).fetchone()
            if existing is not None:
                existing_job_id = row_str(existing, "job_id")
                merged_priority = max(row_int(existing, "priority"), priority)
                conn.execute(
                    """
                    UPDATE file_enrich_queue
                    SET content_hash = :content_hash,
                        priority = :priority,
                        enqueue_source = :enqueue_source,
                        status = 'PENDING',
                        next_retry_at = :next_retry_at,
                        updated_at = :updated_at,
                        last_error = NULL
                    WHERE job_id = :job_id
                    """,
                    {
                        "job_id": existing_job_id,
                        "content_hash": content_hash,
                        "priority": merged_priority,
                        "enqueue_source": enqueue_source,
                        "next_retry_at": now_iso,
                        "updated_at": now_iso,
                    },
                )
                conn.commit()
                return existing_job_id
            conn.execute(
                """
                INSERT INTO file_enrich_queue(
                    job_id, repo_id, repo_root, relative_path, content_hash, content_raw, content_encoding,
                    priority, enqueue_source, status, attempt_count, last_error, next_retry_at, created_at, updated_at
                )
                VALUES(
                    :job_id, :repo_id, :repo_root, :relative_path, :content_hash, '', 'utf-8',
                    :priority, :enqueue_source, :status, :attempt_count, :last_error, :next_retry_at, :created_at, :updated_at
                )
                """,
                job.to_sql_params(),
            )
            conn.commit()
        return job_id

    def enqueue_many(self, requests: list[EnqueueRequestDTO]) -> list[str]:
        """L2 본문 보강 큐 작업을 배치 적재한다."""
        if len(requests) == 0:
            return []
        enqueued_ids: list[str] = []
        with connect(self._db_path) as conn:
            for request in requests:
                resolved_repo_id = (
                    request.repo_id
                    if request.repo_id.strip() != ""
                    else f"r_{uuid.uuid5(uuid.NAMESPACE_URL, request.repo_root).hex[:20]}"
                )
                job_id = str(uuid.uuid4())
                job = FileEnrichJobDTO(
                    job_id=job_id,
                    repo_id=resolved_repo_id,
                    repo_root=request.repo_root,
                    relative_path=request.relative_path,
                    content_hash=request.content_hash,
                    priority=request.priority,
                    enqueue_source=request.enqueue_source,
                    status="PENDING",
                    attempt_count=0,
                    last_error=None,
                    next_retry_at=request.now_iso,
                    created_at=request.now_iso,
                    updated_at=request.now_iso,
                )
                existing = conn.execute(
                    """
                    SELECT job_id, priority
                    FROM file_enrich_queue
                    WHERE repo_id = :repo_id
                      AND repo_root = :repo_root
                      AND relative_path = :relative_path
                      AND status IN ('PENDING', 'FAILED')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    {"repo_id": resolved_repo_id, "repo_root": request.repo_root, "relative_path": request.relative_path},
                ).fetchone()
                if existing is not None:
                    existing_job_id = row_str(existing, "job_id")
                    merged_priority = max(row_int(existing, "priority"), request.priority)
                    conn.execute(
                        """
                        UPDATE file_enrich_queue
                        SET content_hash = :content_hash,
                            priority = :priority,
                            enqueue_source = :enqueue_source,
                            status = 'PENDING',
                            next_retry_at = :next_retry_at,
                            updated_at = :updated_at,
                            last_error = NULL
                        WHERE job_id = :job_id
                        """,
                        {
                            "job_id": existing_job_id,
                            "content_hash": request.content_hash,
                            "priority": merged_priority,
                            "enqueue_source": request.enqueue_source,
                            "next_retry_at": request.now_iso,
                            "updated_at": request.now_iso,
                        },
                    )
                    enqueued_ids.append(existing_job_id)
                    continue
                conn.execute(
                    """
                    INSERT INTO file_enrich_queue(
                        job_id, repo_id, repo_root, relative_path, content_hash, content_raw, content_encoding,
                        priority, enqueue_source, status, attempt_count, last_error, next_retry_at, created_at, updated_at
                    )
                    VALUES(
                        :job_id, :repo_id, :repo_root, :relative_path, :content_hash, '', 'utf-8',
                        :priority, :enqueue_source, :status, :attempt_count, :last_error, :next_retry_at, :created_at, :updated_at
                    )
                    """,
                    job.to_sql_params(),
                )
                enqueued_ids.append(job_id)
            conn.commit()
        return enqueued_ids

    def acquire_pending(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """처리 가능한 보강 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="all")

    def acquire_pending_for_l2(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """L2 단계에서 처리할 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="l2")

    def acquire_pending_for_l3(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """L3 단계에서 처리할 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="l3")

    def _acquire_pending_internal(self, limit: int, now_iso: str, source_mode: str) -> list[FileEnrichJobDTO]:
        """단계 소스 조건에 맞는 보강 작업을 RUNNING으로 전환하고 반환한다."""
        source_clause = ""
        if source_mode == "l2":
            source_clause = "AND enqueue_source <> 'l3'"
        elif source_mode == "l3":
            source_clause = "AND enqueue_source = 'l3'"
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                WITH picked AS (
                    SELECT job_id
                    FROM file_enrich_queue
                    WHERE status IN ('PENDING', 'FAILED')
                      AND next_retry_at <= :now_iso
                      {source_clause}
                    ORDER BY priority DESC, next_retry_at ASC, created_at ASC
                    LIMIT :limit
                )
                UPDATE file_enrich_queue
                SET status = 'RUNNING',
                    updated_at = :now_iso
                WHERE job_id IN (SELECT job_id FROM picked)
                  AND status IN ('PENDING', 'FAILED')
                  AND next_retry_at <= :now_iso
                RETURNING job_id, repo_id, repo_root, relative_path, content_hash, priority, enqueue_source,
                          status, attempt_count, last_error, next_retry_at, created_at, updated_at
                """,
                {"limit": limit, "now_iso": now_iso},
            ).fetchall()

            jobs: list[FileEnrichJobDTO] = []
            for row in rows:
                jobs.append(
                    FileEnrichJobDTO(
                        job_id=row_str(row, "job_id"),
                        repo_id=row_str(row, "repo_id"),
                        repo_root=row_str(row, "repo_root"),
                        relative_path=row_str(row, "relative_path"),
                        content_hash=row_str(row, "content_hash"),
                        priority=row_int(row, "priority"),
                        enqueue_source=row_str(row, "enqueue_source"),
                        status=row_str(row, "status"),
                        attempt_count=row_int(row, "attempt_count"),
                        last_error=row_optional_str(row, "last_error"),
                        next_retry_at=row_str(row, "next_retry_at"),
                        created_at=row_str(row, "created_at"),
                        updated_at=row_str(row, "updated_at"),
                    )
                )
            jobs.sort(key=lambda item: (-item.priority, item.next_retry_at, item.created_at))
            conn.commit()
            return jobs

    def promote_to_l3(self, job_id: str, now_iso: str) -> None:
        """L2 완료 작업을 L3 대기 상태로 승격한다."""
        self.promote_to_l3_many(job_ids=[job_id], now_iso=now_iso)

    def promote_to_l3_many(self, job_ids: list[str], now_iso: str) -> None:
        """L2 완료 작업들을 L3 대기 상태로 배치 승격한다."""
        if len(job_ids) == 0:
            return
        with connect(self._db_path) as conn:
            for job_id in job_ids:
                conn.execute(
                    """
                    UPDATE file_enrich_queue
                    SET status = 'PENDING',
                        enqueue_source = 'l3',
                        attempt_count = 0,
                        last_error = NULL,
                        next_retry_at = :now_iso,
                        updated_at = :now_iso
                    WHERE job_id = :job_id
                    """,
                    {"job_id": job_id, "now_iso": now_iso},
                )
            conn.commit()

    def mark_done(self, job_id: str) -> None:
        """보강 작업을 완료 상태로 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute("UPDATE file_enrich_queue SET status = 'DONE' WHERE job_id = :job_id", {"job_id": job_id})
            conn.commit()

    def mark_done_many(self, job_ids: list[str]) -> None:
        """보강 작업 완료 상태를 배치로 갱신한다."""
        if len(job_ids) == 0:
            return
        with connect(self._db_path) as conn:
            for job_id in job_ids:
                conn.execute("UPDATE file_enrich_queue SET status = 'DONE' WHERE job_id = :job_id", {"job_id": job_id})
            conn.commit()

    def mark_failed(self, job_id: str, error_message: str, next_retry_at: str, dead_threshold: int) -> None:
        """보강 작업 실패 상태를 갱신한다."""
        self.mark_failed_with_backoff(
            job_id=job_id,
            error_message=error_message,
            now_iso=next_retry_at,
            dead_threshold=dead_threshold,
            backoff_base_sec=1,
        )

    def mark_failed_with_backoff(
        self,
        job_id: str,
        error_message: str,
        now_iso: str,
        dead_threshold: int,
        backoff_base_sec: int,
    ) -> None:
        """보강 작업 실패 상태를 백오프 정책으로 갱신한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT attempt_count FROM file_enrich_queue WHERE job_id = :job_id",
                {"job_id": job_id},
            ).fetchone()
            if row is None:
                return
            attempt_count = row_int(row, "attempt_count") + 1
            status = "DEAD" if attempt_count >= dead_threshold else "FAILED"
            next_retry_at = self._compute_next_retry_at(now_iso=now_iso, attempt_count=attempt_count, backoff_base_sec=backoff_base_sec)
            conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = :status,
                    attempt_count = :attempt_count,
                    last_error = :last_error,
                    next_retry_at = :next_retry_at,
                    updated_at = :updated_at
                WHERE job_id = :job_id
                """,
                {
                    "job_id": job_id,
                    "status": status,
                    "attempt_count": attempt_count,
                    "last_error": error_message,
                    "next_retry_at": next_retry_at,
                    "updated_at": next_retry_at,
                },
            )
            conn.commit()

    def mark_failed_with_backoff_many(self, updates: list[FileEnrichFailureUpdateDTO]) -> None:
        """보강 작업 실패 상태를 배치 백오프로 갱신한다."""
        if len(updates) == 0:
            return
        with connect(self._db_path) as conn:
            for update in updates:
                row = conn.execute(
                    "SELECT attempt_count FROM file_enrich_queue WHERE job_id = :job_id",
                    {"job_id": update.job_id},
                ).fetchone()
                if row is None:
                    continue
                attempt_count = row_int(row, "attempt_count") + 1
                status = "DEAD" if attempt_count >= update.dead_threshold else "FAILED"
                next_retry_at = self._compute_next_retry_at(
                    now_iso=update.now_iso,
                    attempt_count=attempt_count,
                    backoff_base_sec=update.backoff_base_sec,
                )
                conn.execute(
                    """
                    UPDATE file_enrich_queue
                    SET status = :status,
                        attempt_count = :attempt_count,
                        last_error = :last_error,
                        next_retry_at = :next_retry_at,
                        updated_at = :updated_at
                    WHERE job_id = :job_id
                    """,
                    {
                        "job_id": update.job_id,
                        "status": status,
                        "attempt_count": attempt_count,
                        "last_error": update.error_message,
                        "next_retry_at": next_retry_at,
                        "updated_at": next_retry_at,
                    },
                )
            conn.commit()

    def get_status_counts(self) -> dict[str, int]:
        """큐 상태별 개수를 반환한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM file_enrich_queue
                GROUP BY status
                """
            ).fetchall()
        counts: dict[str, int] = {"PENDING": 0, "RUNNING": 0, "FAILED": 0, "DONE": 0, "DEAD": 0}
        for row in rows:
            status = row_str(row, "status")
            counts[status] = row_int(row, "cnt")
        return counts

    def get_pending_split_counts(self, now_iso: str) -> dict[str, int]:
        """현재 시각 기준 PENDING을 실행 가능/지연 상태로 분리 집계한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'PENDING' AND next_retry_at <= :now_iso THEN 1 ELSE 0 END) AS pending_available,
                    SUM(CASE WHEN status = 'PENDING' AND next_retry_at > :now_iso THEN 1 ELSE 0 END) AS pending_deferred
                FROM file_enrich_queue
                """,
                {"now_iso": now_iso},
            ).fetchone()
        if row is None:
            return {"PENDING_AVAILABLE": 0, "PENDING_DEFERRED": 0}
        pending_available = int(row["pending_available"] or 0)
        pending_deferred = int(row["pending_deferred"] or 0)
        return {"PENDING_AVAILABLE": pending_available, "PENDING_DEFERRED": pending_deferred}

    def get_pending_age_stats(self, now_iso: str) -> dict[str, float | None]:
        """현재 시각 기준 PENDING available/deferred 작업의 age 통계를 계산한다."""
        now_dt = self._parse_iso_utc(now_iso)
        if now_dt is None:
            return {
                "oldest_pending_available_age_sec": None,
                "oldest_pending_deferred_age_sec": None,
                "p95_pending_available_age_sec": None,
            }
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT created_at, next_retry_at
                FROM file_enrich_queue
                WHERE status = 'PENDING'
                """
            ).fetchall()
        available_ages: list[float] = []
        deferred_ages: list[float] = []
        for row in rows:
            created_at = row_optional_str(row, "created_at")
            next_retry_at = row_optional_str(row, "next_retry_at")
            if created_at is None or next_retry_at is None:
                continue
            created_dt = self._parse_iso_utc(created_at)
            retry_dt = self._parse_iso_utc(next_retry_at)
            if created_dt is None or retry_dt is None:
                continue
            try:
                age_sec = max(0.0, float((now_dt - created_dt).total_seconds()))
            except TypeError:
                continue
            if retry_dt <= now_dt:
                available_ages.append(age_sec)
            else:
                deferred_ages.append(age_sec)

        return {
            "oldest_pending_available_age_sec": max(available_ages) if available_ages else None,
            "oldest_pending_deferred_age_sec": max(deferred_ages) if deferred_ages else None,
            "p95_pending_available_age_sec": self._percentile(available_ages, 95.0),
        }

    def reset_running_to_failed(self, now_iso: str) -> int:
        """비정상 종료 대비 RUNNING 상태를 FAILED로 복구한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'FAILED',
                    last_error = COALESCE(last_error, 'worker interrupted'),
                    next_retry_at = :now_iso,
                    updated_at = :now_iso
                WHERE status = 'RUNNING'
                """,
                {"now_iso": now_iso},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def recover_stale_running_to_failed(self, now_iso: str, stale_before_iso: str) -> int:
        """오래된 RUNNING 작업만 FAILED로 복구한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'FAILED',
                    last_error = COALESCE(last_error, 'worker interrupted'),
                    next_retry_at = :now_iso,
                    updated_at = :now_iso
                WHERE status = 'RUNNING'
                  AND updated_at <= :stale_before_iso
                """,
                {"now_iso": now_iso, "stale_before_iso": stale_before_iso},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def list_dead(self, repo_root: str, limit: int) -> list[DeadJobItemDTO]:
        """저장소 기준 DEAD 작업 목록을 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT job_id, repo_root, relative_path, attempt_count, last_error, updated_at
                FROM file_enrich_queue
                WHERE repo_root = :repo_root
                  AND status = 'DEAD'
                ORDER BY updated_at ASC
                LIMIT :limit
                """,
                {"repo_root": repo_root, "limit": limit},
            ).fetchall()
        return [
            DeadJobItemDTO(
                job_id=row_str(row, "job_id"),
                repo_root=row_str(row, "repo_root"),
                relative_path=row_str(row, "relative_path"),
                attempt_count=row_int(row, "attempt_count"),
                last_error=row_optional_str(row, "last_error"),
                updated_at=row_str(row, "updated_at"),
            )
            for row in rows
        ]

    def requeue_dead(self, repo_root: str, limit: int, now_iso: str) -> int:
        """저장소 기준 DEAD 작업을 FAILED로 재큐잉한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'FAILED',
                    next_retry_at = :now_iso,
                    updated_at = :now_iso,
                    last_error = COALESCE(last_error, 'requeued by operator')
                WHERE job_id IN (
                    SELECT job_id
                    FROM file_enrich_queue
                    WHERE repo_root = :repo_root
                      AND status = 'DEAD'
                    ORDER BY updated_at ASC
                    LIMIT :limit
                )
                """,
                {"repo_root": repo_root, "limit": limit, "now_iso": now_iso},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def requeue_dead_all(self, now_iso: str) -> int:
        """저장소 조건 없이 DEAD 작업 전체를 FAILED로 재큐잉한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'FAILED',
                    next_retry_at = :now_iso,
                    updated_at = :now_iso,
                    last_error = COALESCE(last_error, 'requeued by operator')
                WHERE status = 'DEAD'
                """,
                {"now_iso": now_iso},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def purge_dead(self, repo_root: str, limit: int) -> int:
        """저장소 기준 DEAD 작업을 삭제한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                DELETE FROM file_enrich_queue
                WHERE job_id IN (
                    SELECT job_id
                    FROM file_enrich_queue
                    WHERE repo_root = :repo_root
                      AND status = 'DEAD'
                    ORDER BY updated_at ASC
                    LIMIT :limit
                )
                """,
                {"repo_root": repo_root, "limit": limit},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def purge_dead_all(self) -> int:
        """저장소 조건 없이 DEAD 작업 전체를 삭제한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM file_enrich_queue WHERE status = 'DEAD'")
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def get_status(self, job_id: str) -> str | None:
        """작업의 현재 상태를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT status FROM file_enrich_queue WHERE job_id = :job_id",
                {"job_id": job_id},
            ).fetchone()
        if row is None:
            return None
        return row_str(row, "status")

    def _compute_next_retry_at(self, now_iso: str, attempt_count: int, backoff_base_sec: int) -> str:
        """재시도 시각을 지수 백오프로 계산한다."""
        try:
            now_dt = datetime.fromisoformat(now_iso)
        except ValueError:
            now_dt = datetime.utcnow()
        delay_sec = backoff_base_sec * (2 ** max(0, attempt_count - 1))
        return (now_dt + timedelta(seconds=delay_sec)).isoformat()

    def _parse_iso(self, value: str) -> datetime | None:
        """ISO8601 문자열을 파싱한다."""
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _parse_iso_utc(self, value: str) -> datetime | None:
        """ISO8601 문자열을 UTC aware datetime으로 정규화한다."""
        parsed = self._parse_iso(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _percentile(self, values: list[float], percentile: float) -> float | None:
        """단순 nearest-rank 방식 백분위를 계산한다."""
        if len(values) == 0:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        rank = max(1, min(len(ordered), int((percentile / 100.0) * len(ordered) + 0.999999)))
        return ordered[rank - 1]
