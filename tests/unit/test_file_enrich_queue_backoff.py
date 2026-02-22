"""L2 큐 백오프/복구 상태 전이를 검증한다."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import connect, init_schema


def _read_queue_row(db_path: Path, job_id: str) -> dict[str, object]:
    """큐 단건 상태를 조회한다."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, attempt_count, next_retry_at, last_error
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
    }


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
