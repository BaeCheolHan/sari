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
        scope_repo_root: str | None = None,
    ) -> str:
        """L2 본문 보강 큐 작업을 적재한다."""
        resolved_repo_id = repo_id if repo_id is not None and repo_id.strip() != "" else f"r_{uuid.uuid5(uuid.NAMESPACE_URL, repo_root).hex[:20]}"
        resolved_scope_repo_root = scope_repo_root if scope_repo_root is not None and scope_repo_root.strip() != "" else repo_root
        job_id = str(uuid.uuid4())
        job = FileEnrichJobDTO(
            job_id=job_id,
            repo_id=resolved_repo_id,
            repo_root=repo_root,
            scope_repo_root=resolved_scope_repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
            priority=priority,
            enqueue_source=enqueue_source,
            status="PENDING",
            attempt_count=0,
            last_error=None,
            defer_reason=None,
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
                        last_error = NULL,
                        defer_reason = NULL,
                        deferred_state = NULL,
                        deferred_count = 0,
                        first_deferred_at = NULL,
                        last_deferred_at = NULL,
                        scope_level = NULL,
                        scope_root = NULL,
                        scope_attempts = 0
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
                    job_id, repo_id, repo_root, scope_repo_root, relative_path, content_hash, content_raw, content_encoding,
                    priority, enqueue_source, status, attempt_count, last_error, defer_reason,
                    scope_level, scope_root, scope_attempts, next_retry_at, created_at, updated_at
                )
                VALUES(
                    :job_id, :repo_id, :repo_root, :scope_repo_root, :relative_path, :content_hash, '', 'utf-8',
                    :priority, :enqueue_source, :status, :attempt_count, :last_error, :defer_reason,
                    :scope_level, :scope_root, :scope_attempts, :next_retry_at, :created_at, :updated_at
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
        grouped_rows: list[dict[str, object]] = []
        stage_id_by_key: dict[tuple[str, str, str], int] = {}
        input_stage_ids: list[int] = []

        for request in requests:
            resolved_repo_id = request.repo_id if request.repo_id.strip() != "" else f"r_{uuid.uuid5(uuid.NAMESPACE_URL, request.repo_root).hex[:20]}"
            resolved_scope_repo_root = request.scope_repo_root if request.scope_repo_root is not None and request.scope_repo_root.strip() != "" else request.repo_root
            key = (resolved_repo_id, request.repo_root, request.relative_path)
            existing_stage_id = stage_id_by_key.get(key)
            if existing_stage_id is None:
                stage_id = len(grouped_rows) + 1
                stage_id_by_key[key] = stage_id
                grouped_rows.append(
                    {
                        "stage_id": stage_id,
                        "repo_id": resolved_repo_id,
                        "repo_root": request.repo_root,
                        "scope_repo_root": resolved_scope_repo_root,
                        "relative_path": request.relative_path,
                        "content_hash": request.content_hash,
                        "priority": request.priority,
                        "enqueue_source": request.enqueue_source,
                        "defer_reason": request.defer_reason,
                        "now_iso": request.now_iso,
                        "new_job_id": str(uuid.uuid4()),
                        "had_hash_transition": 0,
                    }
                )
                input_stage_ids.append(stage_id)
                continue
            stage_row = grouped_rows[existing_stage_id - 1]
            previous_hash = str(stage_row["content_hash"])
            if previous_hash != request.content_hash:
                stage_row["had_hash_transition"] = 1
            stage_row["scope_repo_root"] = resolved_scope_repo_root
            stage_row["content_hash"] = request.content_hash
            stage_row["enqueue_source"] = request.enqueue_source
            stage_row["defer_reason"] = request.defer_reason
            stage_row["now_iso"] = request.now_iso
            stage_row["priority"] = max(int(stage_row["priority"]), request.priority)
            input_stage_ids.append(existing_stage_id)

        with connect(self._db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS temp_enqueue_stage")
            conn.execute("DROP TABLE IF EXISTS temp_enqueue_existing")
            conn.execute("DROP TABLE IF EXISTS temp_enqueue_result")
            conn.execute(
                """
                CREATE TEMP TABLE temp_enqueue_stage (
                    stage_id INTEGER PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    repo_root TEXT NOT NULL,
                    scope_repo_root TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    enqueue_source TEXT NOT NULL,
                    defer_reason TEXT NULL,
                    now_iso TEXT NOT NULL,
                    new_job_id TEXT NOT NULL,
                    had_hash_transition INTEGER NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO temp_enqueue_stage(
                    stage_id, repo_id, repo_root, scope_repo_root, relative_path,
                    content_hash, priority, enqueue_source, defer_reason, now_iso, new_job_id, had_hash_transition
                ) VALUES (
                    :stage_id, :repo_id, :repo_root, :scope_repo_root, :relative_path,
                    :content_hash, :priority, :enqueue_source, :defer_reason, :now_iso, :new_job_id, :had_hash_transition
                )
                """,
                grouped_rows,
            )
            conn.execute(
                """
                CREATE TEMP TABLE temp_enqueue_existing (
                    stage_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    content_hash TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO temp_enqueue_existing(stage_id, job_id, priority, content_hash)
                WITH candidate AS (
                    SELECT
                        s.stage_id AS stage_id,
                        q.job_id AS job_id,
                        q.priority AS priority,
                        q.content_hash AS content_hash,
                        ROW_NUMBER() OVER (
                            PARTITION BY s.stage_id
                            ORDER BY q.updated_at DESC, q.job_id DESC
                        ) AS rn
                    FROM temp_enqueue_stage AS s
                    JOIN file_enrich_queue AS q
                      ON q.repo_id = s.repo_id
                     AND q.repo_root = s.repo_root
                     AND q.relative_path = s.relative_path
                     AND q.status IN ('PENDING', 'FAILED')
                )
                SELECT stage_id, job_id, priority, content_hash
                FROM candidate
                WHERE rn = 1
                """
            )
            conn.execute(
                """
                UPDATE file_enrich_queue AS q
                SET content_hash = s.content_hash,
                    priority = MAX(e.priority, s.priority),
                    enqueue_source = s.enqueue_source,
                    status = 'PENDING',
                    updated_at = s.now_iso,
                    next_retry_at = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN s.now_iso
                        ELSE q.next_retry_at
                    END,
                    defer_reason = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.defer_reason
                    END,
                    deferred_state = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.deferred_state
                    END,
                    deferred_count = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN 0
                        ELSE q.deferred_count
                    END,
                    first_deferred_at = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.first_deferred_at
                    END,
                    last_deferred_at = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.last_deferred_at
                    END,
                    last_error = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.last_error
                    END,
                    scope_level = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.scope_level
                    END,
                    scope_root = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN NULL
                        ELSE q.scope_root
                    END,
                    scope_attempts = CASE
                        WHEN (e.content_hash <> s.content_hash OR s.had_hash_transition = 1) THEN 0
                        ELSE q.scope_attempts
                    END
                FROM temp_enqueue_stage AS s
                JOIN temp_enqueue_existing AS e
                  ON e.stage_id = s.stage_id
                WHERE q.job_id = e.job_id
                """
            )
            conn.execute(
                """
                INSERT INTO file_enrich_queue(
                    job_id, repo_id, repo_root, scope_repo_root, relative_path, content_hash, content_raw, content_encoding,
                    priority, enqueue_source, status, attempt_count, last_error, defer_reason,
                    scope_level, scope_root, scope_attempts, next_retry_at, created_at, updated_at
                )
                SELECT
                    s.new_job_id,
                    s.repo_id,
                    s.repo_root,
                    s.scope_repo_root,
                    s.relative_path,
                    s.content_hash,
                    '',
                    'utf-8',
                    s.priority,
                    s.enqueue_source,
                    'PENDING',
                    0,
                    NULL,
                    s.defer_reason,
                    NULL,
                    NULL,
                    0,
                    s.now_iso,
                    s.now_iso,
                    s.now_iso
                FROM temp_enqueue_stage AS s
                LEFT JOIN temp_enqueue_existing AS e
                  ON e.stage_id = s.stage_id
                WHERE e.stage_id IS NULL
                """
            )
            conn.execute(
                """
                CREATE TEMP TABLE temp_enqueue_result (
                    stage_id INTEGER PRIMARY KEY,
                    job_id TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO temp_enqueue_result(stage_id, job_id)
                SELECT stage_id, job_id
                FROM temp_enqueue_existing
                """
            )
            conn.execute(
                """
                INSERT INTO temp_enqueue_result(stage_id, job_id)
                SELECT s.stage_id, s.new_job_id
                FROM temp_enqueue_stage AS s
                LEFT JOIN temp_enqueue_existing AS e
                  ON e.stage_id = s.stage_id
                WHERE e.stage_id IS NULL
                """
            )
            rows = conn.execute(
                """
                SELECT stage_id, job_id
                FROM temp_enqueue_result
                """
            ).fetchall()
            stage_to_job_id = {int(row["stage_id"]): row_str(row, "job_id") for row in rows}
            conn.commit()
        return [stage_to_job_id[stage_id] for stage_id in input_stage_ids]

    def acquire_pending(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """처리 가능한 보강 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="all")

    def acquire_pending_for_l2(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """L2 단계에서 처리할 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="l2")

    def acquire_pending_for_l3(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """L3 단계에서 처리할 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="l3")

    def acquire_pending_for_l5(self, limit: int, now_iso: str) -> list[FileEnrichJobDTO]:
        """L5 단계에서 처리할 작업을 RUNNING으로 전환하고 반환한다."""
        return self._acquire_pending_internal(limit=limit, now_iso=now_iso, source_mode="l5")

    def _acquire_pending_internal(self, limit: int, now_iso: str, source_mode: str) -> list[FileEnrichJobDTO]:
        """단계 소스 조건에 맞는 보강 작업을 RUNNING으로 전환하고 반환한다."""
        source_clause = ""
        if source_mode == "all":
            source_clause = "AND enqueue_source <> 'l5'"
        elif source_mode == "l2":
            source_clause = "AND enqueue_source NOT IN ('l3', 'l5')"
        elif source_mode == "l3":
            source_clause = "AND enqueue_source = 'l3'"
        elif source_mode == "l5":
            source_clause = "AND enqueue_source = 'l5'"
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
                          status, attempt_count, last_error, defer_reason,
                          deferred_state, deferred_count, first_deferred_at, last_deferred_at,
                          scope_level, scope_root, scope_attempts,
                          next_retry_at, created_at, updated_at
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
                        defer_reason=row_optional_str(row, "defer_reason"),
                        deferred_state=row_optional_str(row, "deferred_state"),
                        deferred_count=row_int(row, "deferred_count"),
                        first_deferred_at=row_optional_str(row, "first_deferred_at"),
                        last_deferred_at=row_optional_str(row, "last_deferred_at"),
                        scope_level=row_optional_str(row, "scope_level"),
                        scope_root=row_optional_str(row, "scope_root"),
                        scope_attempts=row_int(row, "scope_attempts"),
                        next_retry_at=row_str(row, "next_retry_at"),
                        created_at=row_str(row, "created_at"),
                        updated_at=row_str(row, "updated_at"),
                    )
                )
            jobs.sort(key=lambda item: (-item.priority, item.next_retry_at, item.created_at))
            conn.commit()
            return jobs

    def handoff_running_to_l5(self, *, job_id: str, now_iso: str) -> bool:
        """RUNNING 작업을 L5 lane(PENDING/l5)으로 이관한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'PENDING',
                    enqueue_source = 'l5',
                    attempt_count = 0,
                    last_error = NULL,
                    next_retry_at = :now_iso,
                    defer_reason = NULL,
                    deferred_state = NULL,
                    deferred_count = 0,
                    first_deferred_at = NULL,
                    last_deferred_at = NULL,
                    updated_at = :now_iso
                WHERE job_id = :job_id
                  AND status = 'RUNNING'
                """,
                {"job_id": job_id, "now_iso": now_iso},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0) > 0

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
                        defer_reason = NULL,
                        deferred_state = NULL,
                        deferred_count = 0,
                        first_deferred_at = NULL,
                        last_deferred_at = NULL,
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

    def mark_done_many(self, job_ids: list[str], *, conn=None) -> None:
        """보강 작업 완료 상태를 배치로 갱신한다."""
        if len(job_ids) == 0:
            return
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
            for job_id in job_ids:
                conn.execute("UPDATE file_enrich_queue SET status = 'DONE' WHERE job_id = :job_id", {"job_id": job_id})
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

    def defer_pending_jobs(self, job_ids: list[str], next_retry_at: str, defer_reason: str, now_iso: str) -> int:
        """broker defer를 PENDING + next_retry_at + defer_reason로 기록한다."""
        if len(job_ids) == 0:
            return 0
        changed = 0
        with connect(self._db_path) as conn:
            for job_id in job_ids:
                row = conn.execute(
                    """
                    UPDATE file_enrich_queue
                    SET status = 'PENDING',
                        next_retry_at = :next_retry_at,
                        defer_reason = :defer_reason,
                        updated_at = :updated_at
                    WHERE job_id = :job_id
                      AND status = 'PENDING'
                    """,
                    {
                        "job_id": job_id,
                        "next_retry_at": next_retry_at,
                        "defer_reason": defer_reason,
                        "updated_at": now_iso,
                    },
                )
                changed += int(row.rowcount if row.rowcount is not None else 0)
            conn.commit()
        return changed

    def defer_jobs_to_pending(
        self,
        job_ids: list[str],
        next_retry_at: str,
        defer_reason: str,
        now_iso: str,
        max_deferred_queue_size: int | None = None,
        max_deferred_per_workspace: int | None = None,
        deferred_ttl_hours: int | None = None,
    ) -> int:
        """RUNNING/PENDING 작업을 broker defer 의미로 PENDING 상태로 되돌린다.

        규칙:
        - status는 PENDING으로 설정
        - next_retry_at/defer_reason만 갱신 (updated_at 포함)
        - attempt_count/error_count는 절대 건드리지 않음
        - deferred 상태머신(NEW/RETRY_WAIT/BUMPED/DROPPED)을 함께 갱신
        - cap/TTL 초과 시 DROP 처리(status=DONE, deferred_state=DROPPED)
        """
        if len(job_ids) == 0:
            return 0
        changed = 0
        now_dt = self._parse_iso_utc(now_iso)
        ttl_limit_dt = None
        if now_dt is not None and deferred_ttl_hours is not None and deferred_ttl_hours > 0:
            ttl_limit_dt = now_dt - timedelta(hours=int(deferred_ttl_hours))
        with connect(self._db_path) as conn:
            for job_id in job_ids:
                current = conn.execute(
                    """
                    SELECT repo_root, deferred_state, deferred_count, first_deferred_at
                    FROM file_enrich_queue
                    WHERE job_id = :job_id
                      AND status IN ('PENDING', 'RUNNING')
                    """,
                    {"job_id": job_id},
                ).fetchone()
                if current is None:
                    continue

                first_deferred_at = row_optional_str(current, "first_deferred_at")
                if ttl_limit_dt is not None and first_deferred_at is not None:
                    first_dt = self._parse_iso_utc(first_deferred_at)
                    if first_dt is not None and first_dt <= ttl_limit_dt:
                        conn.execute(
                            """
                            UPDATE file_enrich_queue
                            SET status = 'DONE',
                                defer_reason = 'l5_drop:ttl_expired',
                                deferred_state = 'DROPPED',
                                last_deferred_at = :updated_at,
                                updated_at = :updated_at
                            WHERE job_id = :job_id
                            """,
                            {"job_id": job_id, "updated_at": now_iso},
                        )
                        continue

                if max_deferred_queue_size is not None or max_deferred_per_workspace is not None:
                    counts = conn.execute(
                        """
                        SELECT
                            SUM(
                                CASE
                                    WHEN status = 'PENDING'
                                     AND next_retry_at > :now_iso
                                     AND deferred_state IN ('NEW', 'RETRY_WAIT', 'BUMPED')
                                    THEN 1 ELSE 0
                                END
                            ) AS total_deferred,
                            SUM(
                                CASE
                                    WHEN repo_root = :repo_root
                                     AND status = 'PENDING'
                                     AND next_retry_at > :now_iso
                                     AND deferred_state IN ('NEW', 'RETRY_WAIT', 'BUMPED')
                                    THEN 1 ELSE 0
                                END
                            ) AS workspace_deferred
                        FROM file_enrich_queue
                        """,
                        {"now_iso": now_iso, "repo_root": row_str(current, "repo_root")},
                    ).fetchone()
                    total_deferred = int(counts["total_deferred"] or 0) if counts is not None else 0
                    workspace_deferred = int(counts["workspace_deferred"] or 0) if counts is not None else 0
                    overflow_total = max_deferred_queue_size is not None and total_deferred >= int(max_deferred_queue_size)
                    overflow_workspace = (
                        max_deferred_per_workspace is not None and workspace_deferred >= int(max_deferred_per_workspace)
                    )
                    if overflow_total or overflow_workspace:
                        drop_reason = "l5_drop:deferred_cap_workspace" if overflow_workspace else "l5_drop:deferred_cap_total"
                        conn.execute(
                            """
                            UPDATE file_enrich_queue
                            SET status = 'DONE',
                                defer_reason = :defer_reason,
                                deferred_state = 'DROPPED',
                                last_deferred_at = :updated_at,
                                updated_at = :updated_at
                            WHERE job_id = :job_id
                            """,
                            {"job_id": job_id, "defer_reason": drop_reason, "updated_at": now_iso},
                        )
                        continue

                prev_state = (row_optional_str(current, "deferred_state") or "").strip().upper()
                prev_count = int(current["deferred_count"] or 0)
                if prev_state == "DROPPED":
                    continue
                next_count = prev_count + 1
                if prev_state == "BUMPED":
                    next_state = "BUMPED"
                elif prev_state in {"NEW", "RETRY_WAIT"}:
                    next_state = "RETRY_WAIT" if next_count <= 2 else "BUMPED"
                else:
                    next_state = "NEW"

                row = conn.execute(
                    """
                    UPDATE file_enrich_queue
                    SET status = 'PENDING',
                        next_retry_at = :next_retry_at,
                        defer_reason = :defer_reason,
                        deferred_state = :deferred_state,
                        deferred_count = :deferred_count,
                        first_deferred_at = COALESCE(first_deferred_at, :first_deferred_at),
                        last_deferred_at = :last_deferred_at,
                        updated_at = :updated_at
                    WHERE job_id = :job_id
                      AND status IN ('PENDING', 'RUNNING')
                    """,
                    {
                        "job_id": job_id,
                        "next_retry_at": next_retry_at,
                        "defer_reason": defer_reason,
                        "deferred_state": next_state,
                        "deferred_count": next_count,
                        "first_deferred_at": now_iso,
                        "last_deferred_at": now_iso,
                        "updated_at": now_iso,
                    },
                )
                changed += int(row.rowcount if row.rowcount is not None else 0)
            conn.commit()
        return changed

    def escalate_scope_on_same_job(
        self,
        job_id: str,
        next_scope_level: str,
        next_scope_root: str,
        next_retry_at: str,
        now_iso: str,
    ) -> bool:
        """동일 queue row를 재사용해 scope escalation 상태를 갱신한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE file_enrich_queue
                SET status = 'PENDING',
                    scope_level = :scope_level,
                    scope_root = :scope_root,
                    scope_attempts = COALESCE(scope_attempts, 0) + 1,
                    next_retry_at = :next_retry_at,
                    defer_reason = NULL,
                    updated_at = :updated_at
                WHERE job_id = :job_id
                """,
                {
                    "job_id": job_id,
                    "scope_level": next_scope_level,
                    "scope_root": next_scope_root,
                    "next_retry_at": next_retry_at,
                    "updated_at": now_iso,
                },
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0) > 0

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

    def mark_failed_with_backoff_many(self, updates: list[FileEnrichFailureUpdateDTO], *, conn=None) -> None:
        """보강 작업 실패 상태를 배치 백오프로 갱신한다."""
        if len(updates) == 0:
            return
        owned_conn = conn is None
        if owned_conn:
            conn = connect(self._db_path)
        if conn is None:
            raise RuntimeError("conn must not be None when owned_conn is False")
        try:
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
            if owned_conn:
                conn.commit()
        finally:
            if owned_conn:
                conn.close()

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
                    SUM(CASE WHEN status = 'PENDING' AND next_retry_at > :now_iso THEN 1 ELSE 0 END) AS pending_deferred,
                    SUM(
                        CASE
                            WHEN status = 'PENDING'
                             AND next_retry_at > :now_iso
                             AND COALESCE(defer_reason, '') LIKE 'l5_defer:tsls_fast:%'
                            THEN 1 ELSE 0
                        END
                    ) AS pending_deferred_fast,
                    SUM(
                        CASE
                            WHEN status = 'PENDING'
                             AND next_retry_at > :now_iso
                             AND COALESCE(defer_reason, '') LIKE 'l5_defer:deferred_heavy:%'
                            THEN 1 ELSE 0
                        END
                    ) AS pending_deferred_heavy
                FROM file_enrich_queue
                """,
                {"now_iso": now_iso},
            ).fetchone()
        if row is None:
            return {
                "PENDING_AVAILABLE": 0,
                "PENDING_DEFERRED": 0,
                "PENDING_DEFERRED_FAST": 0,
                "PENDING_DEFERRED_HEAVY": 0,
            }
        pending_available = int(row["pending_available"] or 0)
        pending_deferred = int(row["pending_deferred"] or 0)
        pending_deferred_fast = int(row["pending_deferred_fast"] or 0)
        pending_deferred_heavy = int(row["pending_deferred_heavy"] or 0)
        return {
            "PENDING_AVAILABLE": pending_available,
            "PENDING_DEFERRED": pending_deferred,
            "PENDING_DEFERRED_FAST": pending_deferred_fast,
            "PENDING_DEFERRED_HEAVY": pending_deferred_heavy,
        }

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

    def get_deferred_drop_stats(self, top_k: int = 10) -> dict[str, object]:
        """deferred DROP 상태의 reason/workspace/language 집계를 반환한다."""
        safe_top_k = max(1, int(top_k))
        with connect(self._db_path) as conn:
            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS dropped_total,
                    SUM(CASE WHEN defer_reason = 'l5_drop:ttl_expired' THEN 1 ELSE 0 END) AS dropped_ttl_expired,
                    SUM(CASE WHEN defer_reason = 'l5_drop:deferred_cap_total' THEN 1 ELSE 0 END) AS dropped_cap_total,
                    SUM(CASE WHEN defer_reason = 'l5_drop:deferred_cap_workspace' THEN 1 ELSE 0 END) AS dropped_cap_workspace
                FROM file_enrich_queue
                WHERE deferred_state = 'DROPPED'
                """
            ).fetchone()

            by_reason_rows = conn.execute(
                """
                SELECT defer_reason, COUNT(*) AS cnt
                FROM file_enrich_queue
                WHERE deferred_state = 'DROPPED'
                GROUP BY defer_reason
                ORDER BY cnt DESC
                LIMIT :limit
                """,
                {"limit": safe_top_k},
            ).fetchall()

            by_workspace_rows = conn.execute(
                """
                SELECT repo_root, COUNT(*) AS cnt
                FROM file_enrich_queue
                WHERE deferred_state = 'DROPPED'
                GROUP BY repo_root
                ORDER BY cnt DESC
                LIMIT :limit
                """,
                {"limit": safe_top_k},
            ).fetchall()

            language_rows = conn.execute(
                """
                SELECT relative_path
                FROM file_enrich_queue
                WHERE deferred_state = 'DROPPED'
                """
            ).fetchall()

        dropped_total = int(totals["dropped_total"] or 0) if totals is not None else 0
        by_reason: dict[str, int] = {}
        for row in by_reason_rows:
            reason = row_optional_str(row, "defer_reason") or "unknown"
            by_reason[reason] = int(row["cnt"] or 0)

        by_workspace_topk: list[dict[str, object]] = []
        for row in by_workspace_rows:
            by_workspace_topk.append(
                {
                    "repo_root": row_str(row, "repo_root"),
                    "count": int(row["cnt"] or 0),
                }
            )

        by_language_acc: dict[str, int] = {}
        for row in language_rows:
            relative_path = row_optional_str(row, "relative_path") or ""
            suffix = Path(relative_path).suffix.lower().lstrip(".")
            language_key = suffix if suffix != "" else "no_ext"
            by_language_acc[language_key] = int(by_language_acc.get(language_key, 0)) + 1
        by_language_topk = [
            {"language": key, "count": value}
            for key, value in sorted(by_language_acc.items(), key=lambda item: item[1], reverse=True)[:safe_top_k]
        ]

        return {
            "dropped_total": dropped_total,
            "dropped_ttl_expired_count": int(totals["dropped_ttl_expired"] or 0) if totals is not None else 0,
            "dropped_cap_total_count": int(totals["dropped_cap_total"] or 0) if totals is not None else 0,
            "dropped_cap_workspace_count": int(totals["dropped_cap_workspace"] or 0) if totals is not None else 0,
            "by_reason": by_reason,
            "by_workspace_topk": by_workspace_topk,
            "by_language_topk": by_language_topk,
        }

    def count_pending_perf_ignorable(self) -> int:
        """perf drain 종료 시 무시 가능한 heavy defer pending 개수를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM file_enrich_queue
                WHERE status = 'PENDING'
                  AND defer_reason = 'l5_defer:deferred_heavy:l3_preprocess_large_file'
                """
            ).fetchone()
        if row is None:
            return 0
        return int(row["cnt"] or 0)

    def list_pending_perf_ignorable_job_ids(self, limit: int = 256) -> list[str]:
        """perf/batch 종료 직전 강제 소진 대상으로 사용할 heavy defer pending job_id 목록."""
        capped_limit = max(1, int(limit))
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT job_id
                FROM file_enrich_queue
                WHERE status = 'PENDING'
                  AND defer_reason = 'l5_defer:deferred_heavy:l3_preprocess_large_file'
                ORDER BY priority DESC, created_at ASC
                LIMIT :limit
                """,
                {"limit": capped_limit},
            ).fetchall()
        return [row_str(row, "job_id") for row in rows]

    def get_eligible_counts(self, now_iso: str) -> dict[str, int]:
        """strict eligible(v1) queue-job 기준 집계를 반환한다.

        Phase B v1 정의:
        - queue job 기준 집계
        - PENDING_DEFERRED는 eligible_total에서 제외
        - DONE은 tool_readiness(tool_ready=1,last_reason='ok',hash match)만 포함
        - FAILED는 영구 unavailable/config/workspace mismatch 계열 제외
        """
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                WITH current_jobs AS (
                    SELECT q.job_id,
                           q.status,
                           q.repo_root,
                           q.relative_path,
                           q.content_hash,
                           q.next_retry_at,
                           q.defer_reason,
                           q.last_error
                    FROM file_enrich_queue q
                    JOIN collected_files_l1 f
                      ON f.repo_root = q.repo_root
                     AND f.relative_path = q.relative_path
                     AND f.is_deleted = 0
                     AND f.content_hash = q.content_hash
                )
                SELECT
                    SUM(
                        CASE
                            WHEN status = 'PENDING'
                             AND (next_retry_at IS NULL OR next_retry_at <= :now_iso)
                            THEN 1 ELSE 0
                        END
                    ) AS pending_available,
                    SUM(
                        CASE
                            WHEN status = 'PENDING'
                             AND next_retry_at > :now_iso
                             AND defer_reason LIKE 'broker_defer:%'
                            THEN 1 ELSE 0
                        END
                    ) AS pending_deferred,
                    SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) AS running_count,
                    SUM(
                        CASE
                            WHEN status = 'DONE'
                             AND EXISTS (
                                 SELECT 1
                                 FROM tool_readiness_state trs
                                 WHERE trs.repo_root = current_jobs.repo_root
                                   AND trs.relative_path = current_jobs.relative_path
                                   AND trs.content_hash = current_jobs.content_hash
                                   AND trs.tool_ready = 1
                                   AND trs.last_reason = 'ok'
                             )
                            THEN 1 ELSE 0
                        END
                    ) AS done_count,
                    SUM(
                        CASE
                            WHEN status = 'FAILED'
                             AND (
                                 last_error IS NULL OR (
                                     last_error NOT LIKE '%ERR_LSP_SERVER_MISSING%'
                                     AND last_error NOT LIKE '%ERR_LSP_SERVER_SPAWN_FAILED%'
                                     AND last_error NOT LIKE '%ERR_RUNTIME_MISMATCH%'
                                     AND last_error NOT LIKE '%ERR_LSP_WORKSPACE_MISMATCH%'
                                     AND last_error NOT LIKE '%ERR_CONFIG_INVALID%'
                                 )
                             )
                            THEN 1 ELSE 0
                        END
                    ) AS failed_count
                FROM current_jobs
                """,
                {"now_iso": now_iso},
            ).fetchone()
        pending_available = int(row["pending_available"]) if row is not None and row["pending_available"] is not None else 0
        pending_deferred = int(row["pending_deferred"]) if row is not None and row["pending_deferred"] is not None else 0
        running = int(row["running_count"]) if row is not None and row["running_count"] is not None else 0
        done = int(row["done_count"]) if row is not None and row["done_count"] is not None else 0
        failed = int(row["failed_count"]) if row is not None and row["failed_count"] is not None else 0
        return {
            "eligible_total_count": pending_available + running + done + failed,
            "eligible_done_count": done,
            "eligible_failed_count": failed,
            "eligible_deferred_count": pending_deferred,
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
