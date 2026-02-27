from __future__ import annotations

from pathlib import Path

from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import connect, init_schema


def _enqueue(repo: FileEnrichQueueRepository, *, repo_root: str, relative_path: str, now_iso: str) -> str:
    return repo.enqueue(
        repo_root=repo_root,
        relative_path=relative_path,
        content_hash=f"h:{relative_path}",
        priority=50,
        enqueue_source="scan",
        now_iso=now_iso,
        repo_id="r1",
    )


def test_handoff_running_job_to_l5_and_reacquire_by_l5_lane(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"
    repo_root = "/repo"
    job_id = _enqueue(repo, repo_root=repo_root, relative_path="a.py", now_iso=now_iso)

    acquired_l2 = repo.acquire_pending_for_l2(limit=1, now_iso=now_iso)
    assert [job.job_id for job in acquired_l2] == [job_id]

    changed = repo.handoff_running_to_l5(job_id=job_id, now_iso="2026-03-01T00:00:01+00:00")
    assert changed is True

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, enqueue_source, defer_reason, deferred_state, deferred_count
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
    assert row is not None
    assert str(row["status"]) == "PENDING"
    assert str(row["enqueue_source"]) == "l5"
    assert row["defer_reason"] is None
    assert row["deferred_state"] is None
    assert int(row["deferred_count"]) == 0

    reacquired_l2 = repo.acquire_pending_for_l2(limit=1, now_iso="2026-03-01T00:00:02+00:00")
    assert reacquired_l2 == []
    acquired_l5 = repo.acquire_pending_for_l5(limit=1, now_iso="2026-03-01T00:00:02+00:00")
    assert [job.job_id for job in acquired_l5] == [job_id]


def test_acquire_pending_for_l2_excludes_l5_source_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"
    repo_root = "/repo"

    scan_job_id = _enqueue(repo, repo_root=repo_root, relative_path="scan.py", now_iso=now_iso)
    l5_job_id = _enqueue(repo, repo_root=repo_root, relative_path="l5.py", now_iso=now_iso)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE file_enrich_queue SET enqueue_source = 'l5' WHERE job_id = :job_id",
            {"job_id": l5_job_id},
        )
        conn.commit()

    acquired_l2 = repo.acquire_pending_for_l2(limit=10, now_iso=now_iso)
    acquired_ids = {job.job_id for job in acquired_l2}
    assert scan_job_id in acquired_ids
    assert l5_job_id not in acquired_ids


def test_acquire_pending_all_excludes_l5_source_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"
    repo_root = "/repo"

    scan_job_id = _enqueue(repo, repo_root=repo_root, relative_path="scan-all.py", now_iso=now_iso)
    l5_job_id = _enqueue(repo, repo_root=repo_root, relative_path="l5-all.py", now_iso=now_iso)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE file_enrich_queue SET enqueue_source = 'l5' WHERE job_id = :job_id",
            {"job_id": l5_job_id},
        )
        conn.commit()

    acquired = repo.acquire_pending(limit=10, now_iso=now_iso)
    acquired_ids = {job.job_id for job in acquired}
    assert scan_job_id in acquired_ids
    assert l5_job_id not in acquired_ids


def test_handoff_running_to_l5_resets_retry_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"
    repo_root = "/repo"
    job_id = _enqueue(repo, repo_root=repo_root, relative_path="retry.py", now_iso=now_iso)

    acquired = repo.acquire_pending_for_l2(limit=1, now_iso=now_iso)
    assert [job.job_id for job in acquired] == [job_id]

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET attempt_count = 3,
                last_error = 'l3 failed',
                deferred_count = 7,
                first_deferred_at = '2026-02-28T00:00:00+00:00',
                last_deferred_at = '2026-02-28T00:00:10+00:00'
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        )
        conn.commit()

    changed = repo.handoff_running_to_l5(job_id=job_id, now_iso="2026-03-01T00:00:01+00:00")
    assert changed is True

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT attempt_count, last_error, deferred_count, first_deferred_at, last_deferred_at
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
    assert row is not None
    assert int(row["attempt_count"]) == 0
    assert row["last_error"] is None
    assert int(row["deferred_count"]) == 0
    assert row["first_deferred_at"] is None
    assert row["last_deferred_at"] is None
