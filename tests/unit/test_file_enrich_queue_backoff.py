"""L2 큐 백오프/복구 상태 전이를 검증한다."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from sari.core.models import EnqueueRequestDTO
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import connect, init_schema


def _read_queue_row(db_path: Path, job_id: str) -> dict[str, object]:
    """큐 단건 상태를 조회한다."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, attempt_count, next_retry_at, last_error
                 , content_hash, defer_reason
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
    if row is None:
        raise AssertionError("queue row가 존재해야 합니다")
    return {
        "status": str(row["status"]),
        "attempt_count": int(row["attempt_count"]),
        "next_retry_at": str(row["next_retry_at"]),
        "last_error": str(row["last_error"]) if row["last_error"] is not None else None,
        "content_hash": str(row["content_hash"]),
        "defer_reason": str(row["defer_reason"]) if row["defer_reason"] is not None else None,
    }


def test_defer_pending_job_keeps_status_pending_and_preserves_attempt_count(tmp_path: Path) -> None:
    """broker defer는 PENDING 상태를 유지하고 attempt_count를 오염시키면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="defer.py",
        content_hash="h-defer",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    repo.defer_pending_jobs(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:05:00+00:00",
        defer_reason="broker_defer:budget",
        now_iso="2026-02-16T00:00:01+00:00",
    )

    row = _read_queue_row(db_path, job_id)
    assert row["status"] == "PENDING"
    assert row["attempt_count"] == 0
    assert row["next_retry_at"] == "2026-02-16T00:05:00+00:00"
    assert row["defer_reason"] == "broker_defer:budget"


def test_enqueue_many_same_hash_preserves_defer_fields(tmp_path: Path) -> None:
    """동일 해시 merge는 defer 필드를 덮어쓰면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="same.py",
        content_hash="h-same",
        priority=10,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    repo.defer_pending_jobs(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:10:00+00:00",
        defer_reason="broker_defer:cooldown",
        now_iso="2026-02-16T00:00:01+00:00",
    )

    _ = repo.enqueue_many(
        [
            # same hash enqueue should not clear defer fields
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="same.py",
                content_hash="h-same",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:02+00:00",
            )
        ]
    )

    row = _read_queue_row(db_path, job_id)
    assert row["defer_reason"] == "broker_defer:cooldown"
    assert row["next_retry_at"] == "2026-02-16T00:10:00+00:00"


def test_enqueue_many_supersede_clears_defer_fields(tmp_path: Path) -> None:
    """해시 변경 supersede merge는 defer 필드를 리셋해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="supersede.py",
        content_hash="h-old",
        priority=10,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    repo.defer_pending_jobs(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:10:00+00:00",
        defer_reason="broker_defer:budget",
        now_iso="2026-02-16T00:00:01+00:00",
    )

    _ = repo.enqueue_many(
        [
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="supersede.py",
                content_hash="h-new",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:02+00:00",
            )
        ]
    )

    row = _read_queue_row(db_path, job_id)
    assert row["content_hash"] == "h-new"
    assert row["defer_reason"] is None
    assert row["next_retry_at"] == "2026-02-16T00:00:02+00:00"


def test_mark_failed_with_backoff_updates_attempt_and_delay(tmp_path: Path) -> None:
    """백오프 실패 처리 시 시도횟수/재시도 시각/상태가 갱신되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    repo.mark_failed_with_backoff(
        job_id=job_id,
        error_message="first failure",
        now_iso=now_iso,
        dead_threshold=3,
        backoff_base_sec=2,
    )

    row = _read_queue_row(db_path, job_id)
    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 1
    assert row["last_error"] == "first failure"
    next_retry = datetime.fromisoformat(str(row["next_retry_at"]))
    assert int(next_retry.timestamp()) - int(datetime.fromisoformat(now_iso).timestamp()) == 2


def test_mark_failed_with_backoff_reaches_dead_state_at_threshold(tmp_path: Path) -> None:
    """실패 횟수가 한도에 도달하면 DEAD로 전환되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="b.py",
        content_hash="h2",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    repo.mark_failed_with_backoff(job_id, "f1", now_iso, dead_threshold=3, backoff_base_sec=1)
    repo.mark_failed_with_backoff(job_id, "f2", now_iso, dead_threshold=3, backoff_base_sec=1)
    repo.mark_failed_with_backoff(job_id, "f3", now_iso, dead_threshold=3, backoff_base_sec=1)

    row = _read_queue_row(db_path, job_id)
    assert row["status"] == "DEAD"
    assert row["attempt_count"] == 3
    assert row["last_error"] == "f3"


def test_reset_running_to_failed_recovers_interrupted_jobs(tmp_path: Path) -> None:
    """RUNNING 작업은 재시작 시 FAILED로 복구되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="c.py",
        content_hash="h3",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    acquired = repo.acquire_pending(limit=1, now_iso=now_iso)
    assert len(acquired) == 1
    assert acquired[0].job_id == job_id

    changed = repo.reset_running_to_failed(now_iso="2026-02-16T00:00:05+00:00")
    assert changed == 1
    row = _read_queue_row(db_path, job_id)
    assert row["status"] == "FAILED"


def test_recover_stale_running_to_failed_updates_only_aged_jobs(tmp_path: Path) -> None:
    """stale 기준보다 오래된 RUNNING 작업만 FAILED로 복구해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    old_job = repo.enqueue(
        repo_root="/repo",
        relative_path="old.py",
        content_hash="h-old",
        priority=30,
        enqueue_source="scan",
        now_iso="2026-02-16T00:00:00+00:00",
    )
    new_job = repo.enqueue(
        repo_root="/repo",
        relative_path="new.py",
        content_hash="h-new",
        priority=30,
        enqueue_source="scan",
        now_iso="2026-02-16T00:00:00+00:00",
    )

    _ = repo.acquire_pending(limit=2, now_iso="2026-02-16T00:00:01+00:00")

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET updated_at = :updated_at
            WHERE job_id = :job_id
            """,
            {"job_id": old_job, "updated_at": "2026-02-16T00:00:02+00:00"},
        )
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET updated_at = :updated_at
            WHERE job_id = :job_id
            """,
            {"job_id": new_job, "updated_at": "2026-02-16T00:00:30+00:00"},
        )
        conn.commit()

    changed = repo.recover_stale_running_to_failed(
        now_iso="2026-02-16T00:01:00+00:00",
        stale_before_iso="2026-02-16T00:00:15+00:00",
    )

    assert changed == 1
    old_row = _read_queue_row(db_path, old_job)
    new_row = _read_queue_row(db_path, new_job)
    assert old_row["status"] == "FAILED"
    assert new_row["status"] == "RUNNING"


def test_acquire_pending_skips_jobs_with_future_next_retry_at(tmp_path: Path) -> None:
    """claim SQL은 미래 next_retry_at 작업을 RUNNING으로 올리면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    ready_job = repo.enqueue(
        repo_root="/repo",
        relative_path="ready.py",
        content_hash="h-ready",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    future_job = repo.enqueue(
        repo_root="/repo",
        relative_path="future.py",
        content_hash="h-future",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE file_enrich_queue SET next_retry_at = :ts WHERE job_id = :job_id",
            {"job_id": future_job, "ts": "2026-02-16T00:10:00+00:00"},
        )
        conn.commit()

    acquired = repo.acquire_pending(limit=10, now_iso=now_iso)
    acquired_ids = {item.job_id for item in acquired}
    assert ready_job in acquired_ids
    assert future_job not in acquired_ids
    assert _read_queue_row(db_path, future_job)["status"] == "PENDING"


def test_pending_split_counts_and_age_stats(tmp_path: Path) -> None:
    """pending available/deferred 분리 집계와 age 지표를 계산해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    base_now = "2026-02-16T00:00:00+00:00"
    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h1",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="b.py",
        content_hash="h2",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="c.py",
        content_hash="h3",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET next_retry_at = '2026-02-16T00:00:00+00:00', created_at = '2026-02-15T23:59:30+00:00'
            WHERE relative_path = 'a.py'
            """
        )
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET next_retry_at = '2026-02-16T00:00:00+00:00', created_at = '2026-02-15T23:59:50+00:00'
            WHERE relative_path = 'b.py'
            """
        )
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET next_retry_at = '2026-02-16T00:10:00+00:00', created_at = '2026-02-15T23:50:00+00:00'
            WHERE relative_path = 'c.py'
            """
        )
        conn.commit()

    split = repo.get_pending_split_counts(now_iso="2026-02-16T00:00:00+00:00")
    assert split["PENDING_AVAILABLE"] == 2
    assert split["PENDING_DEFERRED"] == 1

    age = repo.get_pending_age_stats(now_iso="2026-02-16T00:00:00+00:00")
    assert age["oldest_pending_available_age_sec"] == pytest.approx(30.0)
    assert age["oldest_pending_deferred_age_sec"] == pytest.approx(600.0)
    assert age["p95_pending_available_age_sec"] is not None


def test_pending_age_stats_handles_mixed_naive_and_aware_timestamps(tmp_path: Path) -> None:
    """naive/aware ISO 문자열이 섞여도 age 계산 helper는 예외 없이 동작해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="mixed.py",
        content_hash="h-mixed",
        priority=10,
        enqueue_source="scan",
        now_iso="2026-02-16T00:00:00+00:00",
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET created_at = '2026-02-15T23:59:30',
                next_retry_at = '2026-02-16T00:00:00+00:00'
            WHERE relative_path = 'mixed.py'
            """
        )
        conn.commit()

    age = repo.get_pending_age_stats(now_iso="2026-02-16T00:00:00+00:00")
    assert age["oldest_pending_available_age_sec"] == pytest.approx(30.0)


def test_get_eligible_counts_excludes_pending_deferred_from_total(tmp_path: Path) -> None:
    """strict eligible(v1) 집계는 PENDING_DEFERRED를 eligible_total에서 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    base_now = "2026-02-16T00:00:00+00:00"
    ready_job = repo.enqueue(
        repo_root="/repo",
        relative_path="ready.py",
        content_hash="h-ready",
        priority=100,
        enqueue_source="scan",
        now_iso=base_now,
    )
    deferred_job = repo.enqueue(
        repo_root="/repo",
        relative_path="deferred.py",
        content_hash="h-deferred",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    done_job = repo.enqueue(
        repo_root="/repo",
        relative_path="done.py",
        content_hash="h-done",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    failed_job = repo.enqueue(
        repo_root="/repo",
        relative_path="failed.py",
        content_hash="h-failed",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    skipped_done_job = repo.enqueue(
        repo_root="/repo",
        relative_path="skip_done.py",
        content_hash="h-skip-done",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    perm_failed_job = repo.enqueue(
        repo_root="/repo",
        relative_path="perm_failed.py",
        content_hash="h-perm-failed",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    _ = repo.acquire_pending(limit=1, now_iso=base_now)  # ready_job -> RUNNING
    repo.mark_done(done_job)
    repo.mark_done(skipped_done_job)
    repo.mark_failed_with_backoff(
        job_id=failed_job,
        error_message="fail",
        now_iso=base_now,
        dead_threshold=3,
        backoff_base_sec=1,
    )
    repo.mark_failed_with_backoff(
        job_id=perm_failed_job,
        error_message="ERR_LSP_SERVER_MISSING: java",
        now_iso=base_now,
        dead_threshold=3,
        backoff_base_sec=1,
    )
    repo.defer_pending_jobs(
        job_ids=[deferred_job],
        next_retry_at="2026-02-16T00:10:00+00:00",
        defer_reason="broker_defer:budget",
        now_iso="2026-02-16T00:00:01+00:00",
    )

    # strict eligible(v1) helper is queue-job based, but it must only count current file/hash rows
    # and DONE rows that have tool_ready(last_reason=ok), excluding deferred/permanent failures.
    with connect(db_path) as conn:
        for rel, chash in [
            ("ready.py", "h-ready"),
            ("deferred.py", "h-deferred"),
            ("done.py", "h-done"),
            ("failed.py", "h-failed"),
            ("skip_done.py", "h-skip-done"),
            ("perm_failed.py", "h-perm-failed"),
        ]:
            conn.execute(
                """
                INSERT INTO collected_files_l1(
                    repo_id, repo_root, relative_path, absolute_path, repo_label,
                    mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
                ) VALUES (
                    '', '/repo', :relative_path, :absolute_path, 'repo',
                    1, 1, :content_hash, 0, :ts, :ts, 'TOOL_READY'
                )
                """,
                {
                    "relative_path": rel,
                    "absolute_path": f"/repo/{rel}",
                    "content_hash": chash,
                    "ts": base_now,
                },
            )
        conn.execute(
            """
            INSERT INTO tool_readiness_state(
                repo_root, relative_path, content_hash,
                list_files_ready, read_file_ready, search_symbol_ready, get_callers_ready,
                consistency_ready, quality_ready, tool_ready, last_reason, updated_at
            ) VALUES (
                '/repo', 'done.py', 'h-done',
                1,1,1,1,1,1,1,'ok', :ts
            )
            """,
            {"ts": base_now},
        )
        conn.execute(
            """
            INSERT INTO tool_readiness_state(
                repo_root, relative_path, content_hash,
                list_files_ready, read_file_ready, search_symbol_ready, get_callers_ready,
                consistency_ready, quality_ready, tool_ready, last_reason, updated_at
            ) VALUES (
                '/repo', 'skip_done.py', 'h-skip-done',
                1,1,1,1,1,1,1,'skip_recent_success', :ts
            )
            """,
            {"ts": base_now},
        )
        conn.commit()

    counts = repo.get_eligible_counts(now_iso=base_now)
    assert counts["eligible_deferred_count"] == 1
    # RUNNING(1) + DONE(1) + FAILED(1) + PENDING_AVAILABLE(0)
    assert counts["eligible_total_count"] == 3
    assert counts["eligible_done_count"] == 1
    assert counts["eligible_failed_count"] == 1


def test_escalate_scope_on_same_job_updates_existing_queue_row_only(tmp_path: Path) -> None:
    """scope escalation은 새 job 생성 없이 동일 queue row만 갱신해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="scope.py",
        content_hash="h-scope",
        priority=10,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    changed = repo.escalate_scope_on_same_job(
        job_id=job_id,
        next_scope_level="repo",
        next_scope_root="/repo/subproj",
        next_retry_at="2026-02-16T00:00:01+00:00",
        now_iso="2026-02-16T00:00:01+00:00",
    )
    assert changed is True

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, scope_level, scope_root, scope_attempts, next_retry_at, defer_reason
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
        count = conn.execute("SELECT COUNT(1) AS cnt FROM file_enrich_queue").fetchone()
    assert row is not None
    assert count is not None and int(count["cnt"]) == 1
    assert str(row["status"]) == "PENDING"
    assert str(row["scope_level"]) == "repo"
    assert str(row["scope_root"]) == "/repo/subproj"
    assert int(row["scope_attempts"]) == 1
    assert str(row["next_retry_at"]) == "2026-02-16T00:00:01+00:00"
    assert row["defer_reason"] is None
