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




def test_non_l5_enqueue_does_not_rewrite_pending_l5_retry_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"
    repo_root = "/repo"

    l5_job_id = repo.enqueue(
        repo_root=repo_root,
        relative_path="same.py",
        content_hash="h1",
        priority=20,
        enqueue_source="l5",
        now_iso=now_iso,
        repo_id="r1",
    )
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE file_enrich_queue SET defer_reason = 'retry_zero_relations', deferred_count = 1, next_retry_at = '2026-03-01T00:00:15+00:00' WHERE job_id = :job_id",
            {"job_id": l5_job_id},
        )
        conn.commit()

    scan_job_id = repo.enqueue(
        repo_root=repo_root,
        relative_path="same.py",
        content_hash="h1",
        priority=50,
        enqueue_source="scan",
        now_iso=now_iso,
        repo_id="r1",
    )

    assert scan_job_id != l5_job_id
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT job_id, enqueue_source, defer_reason, deferred_count, status FROM file_enrich_queue WHERE repo_root = :repo_root AND relative_path = 'same.py' ORDER BY enqueue_source, job_id",
            {"repo_root": repo_root},
        ).fetchall()
    assert len(rows) == 2
    l5_rows = [row for row in rows if str(row["enqueue_source"]) == "l5"]
    assert len(l5_rows) == 1
    assert str(l5_rows[0]["defer_reason"]) == "retry_zero_relations"
    assert int(l5_rows[0]["deferred_count"]) == 1


from sari.core.models import EnqueueRequestDTO


def test_enqueue_many_preserves_lane_separation_for_same_file(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-03-01T00:00:00+00:00"

    ids = repo.enqueue_many([
        EnqueueRequestDTO(
            repo_id="r1",
            repo_root="/repo",
            relative_path="same-batch.py",
            content_hash="h1",
            priority=50,
            enqueue_source="scan",
            now_iso=now_iso,
        ),
        EnqueueRequestDTO(
            repo_id="r1",
            repo_root="/repo",
            relative_path="same-batch.py",
            content_hash="h1",
            priority=20,
            enqueue_source="l5",
            now_iso=now_iso,
            defer_reason="retry_zero_relations",
        ),
    ])

    assert len(ids) == 2
    assert ids[0] != ids[1]
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT job_id, enqueue_source, defer_reason FROM file_enrich_queue WHERE repo_root = '/repo' AND relative_path = 'same-batch.py' ORDER BY enqueue_source, job_id"
        ).fetchall()
    assert len(rows) == 2
    by_source = {str(row["enqueue_source"]): row for row in rows}
    assert str(by_source["l5"]["defer_reason"]) == "retry_zero_relations"
    scan_reason = by_source["scan"]["defer_reason"]
    assert scan_reason is None
