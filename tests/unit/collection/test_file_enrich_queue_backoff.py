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
                 , deferred_state, deferred_count
                 , first_deferred_at, last_deferred_at
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
        "deferred_state": str(row["deferred_state"]) if row["deferred_state"] is not None else None,
        "deferred_count": int(row["deferred_count"]),
        "first_deferred_at": str(row["first_deferred_at"]) if row["first_deferred_at"] is not None else None,
        "last_deferred_at": str(row["last_deferred_at"]) if row["last_deferred_at"] is not None else None,
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


def test_defer_running_job_to_pending_preserves_counters(tmp_path: Path) -> None:
    """RUNNING 작업을 broker defer로 PENDING 복귀시켜도 attempt/error 카운터를 오염시키면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="running.py",
        content_hash="h-running",
        priority=30,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    _ = repo.acquire_pending(limit=1, now_iso=now_iso)  # status -> RUNNING

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET attempt_count = 2, last_error = 'old error'
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        )
        conn.commit()

    changed = repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="broker_defer:budget",
        now_iso="2026-02-16T00:00:01+00:00",
    )

    assert changed == 1
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, attempt_count, defer_reason, next_retry_at, last_error
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
    assert row is not None
    assert str(row["status"]) == "PENDING"
    assert int(row["attempt_count"]) == 2
    assert str(row["defer_reason"]) == "broker_defer:budget"
    assert str(row["next_retry_at"]) == "2026-02-16T00:01:00+00:00"
    assert str(row["last_error"]) == "old error"


def test_defer_jobs_to_pending_tracks_deferred_state_machine(tmp_path: Path) -> None:
    """defer 반복 시 deferred_state/카운터가 NEW -> RETRY_WAIT -> BUMPED로 전이되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="state.py",
        content_hash="h-state",
        priority=10,
        enqueue_source="l3",
        now_iso=now_iso,
    )
    _ = repo.acquire_pending(limit=1, now_iso=now_iso)
    repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:00:10+00:00",
    )
    row1 = _read_queue_row(db_path, job_id)
    assert row1["deferred_state"] == "NEW"
    assert row1["deferred_count"] == 1

    _ = repo.acquire_pending(limit=1, now_iso="2026-02-16T00:01:00+00:00")
    repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:02:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:01:10+00:00",
    )
    row2 = _read_queue_row(db_path, job_id)
    assert row2["deferred_state"] == "RETRY_WAIT"
    assert row2["deferred_count"] == 2

    _ = repo.acquire_pending(limit=1, now_iso="2026-02-16T00:02:00+00:00")
    repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:03:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:02:10+00:00",
    )
    row3 = _read_queue_row(db_path, job_id)
    assert row3["deferred_state"] == "BUMPED"
    assert row3["deferred_count"] == 3


def test_defer_jobs_to_pending_drops_when_cap_exceeded(tmp_path: Path) -> None:
    """deferred cap 초과 시 신규 defer는 DROP(DONE + DROPPED) 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    j1 = repo.enqueue("/repo", "a.py", "ha", 10, "l3", now_iso)
    j2 = repo.enqueue("/repo", "b.py", "hb", 10, "l3", now_iso)
    _ = repo.acquire_pending(limit=2, now_iso=now_iso)

    changed1 = repo.defer_jobs_to_pending(
        job_ids=[j1],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_burst_exceeded",
        now_iso="2026-02-16T00:00:10+00:00",
        max_deferred_queue_size=1,
        max_deferred_per_workspace=10,
        deferred_ttl_hours=168,
    )
    changed2 = repo.defer_jobs_to_pending(
        job_ids=[j2],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_burst_exceeded",
        now_iso="2026-02-16T00:00:20+00:00",
        max_deferred_queue_size=1,
        max_deferred_per_workspace=10,
        deferred_ttl_hours=168,
    )

    assert changed1 == 1
    assert changed2 == 0
    row2 = _read_queue_row(db_path, j2)
    assert row2["status"] == "DONE"
    assert row2["deferred_state"] == "DROPPED"
    assert row2["defer_reason"] == "l5_drop:deferred_cap_total"


def test_defer_jobs_to_pending_drops_when_ttl_expired(tmp_path: Path) -> None:
    """기존 defer가 TTL을 초과하면 재defer 시 DROP 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue("/repo", "ttl.py", "h-ttl", 10, "l3", now_iso)
    _ = repo.acquire_pending(limit=1, now_iso=now_iso)
    repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:00:10+00:00",
    )
    _ = repo.acquire_pending(limit=1, now_iso="2026-02-16T00:01:00+00:00")
    changed = repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at="2026-02-16T02:00:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T01:30:10+00:00",
        deferred_ttl_hours=1,
    )
    assert changed == 0
    row = _read_queue_row(db_path, job_id)
    assert row["status"] == "DONE"
    assert row["deferred_state"] == "DROPPED"
    assert row["defer_reason"] == "l5_drop:ttl_expired"


def test_get_deferred_drop_stats_returns_reason_workspace_language_breakdown(tmp_path: Path) -> None:
    """deferred DROP 집계는 reason/workspace/language top-k를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"

    j1 = repo.enqueue("/repo-a", "a.py", "h-a", 10, "l3", now_iso)
    j2 = repo.enqueue("/repo-a", "b.js", "h-b", 10, "l3", now_iso)
    j3 = repo.enqueue("/repo-b", "c.py", "h-c", 10, "l3", now_iso)
    _ = repo.acquire_pending(limit=3, now_iso=now_iso)

    _ = repo.defer_jobs_to_pending(
        job_ids=[j1],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:00:10+00:00",
        max_deferred_queue_size=0,
        max_deferred_per_workspace=10,
    )
    _ = repo.defer_jobs_to_pending(
        job_ids=[j2],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_burst_exceeded",
        now_iso="2026-02-16T00:00:20+00:00",
        max_deferred_queue_size=1,
        max_deferred_per_workspace=10,
    )
    _ = repo.defer_jobs_to_pending(
        job_ids=[j3],
        next_retry_at="2026-02-16T00:01:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T00:00:30+00:00",
        max_deferred_queue_size=10,
        max_deferred_per_workspace=10,
    )
    _ = repo.acquire_pending(limit=1, now_iso="2026-02-16T00:01:00+00:00")
    _ = repo.defer_jobs_to_pending(
        job_ids=[j3],
        next_retry_at="2026-02-16T03:00:00+00:00",
        defer_reason="l5_defer:pressure_rate_exceeded",
        now_iso="2026-02-16T02:00:00+00:00",
        deferred_ttl_hours=1,
    )

    stats = repo.get_deferred_drop_stats(top_k=5)
    assert stats["dropped_total"] >= 2
    assert stats["dropped_cap_total_count"] >= 1
    assert stats["dropped_ttl_expired_count"] >= 1
    assert isinstance(stats["by_reason"], dict)
    assert stats["by_reason"].get("l5_drop:deferred_cap_total", 0) >= 1
    assert stats["by_reason"].get("l5_drop:ttl_expired", 0) >= 1
    by_workspace = {item["repo_root"]: int(item["count"]) for item in stats["by_workspace_topk"]}
    assert by_workspace.get("/repo-a", 0) >= 1
    by_language = {item["language"]: int(item["count"]) for item in stats["by_language_topk"]}
    assert by_language.get("py", 0) >= 1


def test_count_pending_perf_ignorable_counts_l5_deferred_heavy_only(tmp_path: Path) -> None:
    """perf 무시 가능 pending은 l5 deferred_heavy만 집계해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    j1 = repo.enqueue("/repo", "big1.js", "h1", 10, "l3", now_iso)
    j2 = repo.enqueue("/repo", "big2.js", "h2", 10, "l3", now_iso)
    j3 = repo.enqueue("/repo", "other.js", "h3", 10, "l3", now_iso)
    _ = repo.acquire_pending(limit=3, now_iso=now_iso)
    repo.defer_jobs_to_pending(
        job_ids=[j1, j2],
        next_retry_at="2026-02-16T01:00:00+00:00",
        defer_reason="l5_defer:deferred_heavy:l3_preprocess_large_file",
        now_iso="2026-02-16T00:00:10+00:00",
    )
    repo.defer_jobs_to_pending(
        job_ids=[j3],
        next_retry_at="2026-02-16T01:00:00+00:00",
        defer_reason="broker_defer:budget",
        now_iso="2026-02-16T00:00:11+00:00",
    )

    assert repo.count_pending_perf_ignorable() == 2


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


def test_enqueue_many_duplicate_hash_oscillation_clears_defer_fields(tmp_path: Path) -> None:
    """동일 key 배치가 A->B->A로 흔들려도 supersede 이력 기준으로 defer를 리셋해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="oscillate.py",
        content_hash="h-a",
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
                relative_path="oscillate.py",
                content_hash="h-a",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:02+00:00",
            ),
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="oscillate.py",
                content_hash="h-b",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:03+00:00",
            ),
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="oscillate.py",
                content_hash="h-a",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:04+00:00",
            ),
        ]
    )

    row = _read_queue_row(db_path, job_id)
    assert row["content_hash"] == "h-a"
    assert row["defer_reason"] is None
    assert row["next_retry_at"] == "2026-02-16T00:00:04+00:00"


def test_enqueue_many_preserves_input_order_and_merges_duplicate_requests(tmp_path: Path) -> None:
    """반환 job_id는 입력 순서를 보존하고 동일 key 요청은 하나의 job으로 병합해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    existing_id = repo.enqueue(
        repo_root="/repo",
        relative_path="a.py",
        content_hash="h-old",
        priority=5,
        enqueue_source="scan",
        now_iso="2026-02-16T00:00:00+00:00",
    )

    ids = repo.enqueue_many(
        [
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="b.py",
                content_hash="h-b1",
                priority=10,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:01+00:00",
            ),
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="a.py",
                content_hash="h-a1",
                priority=20,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:02+00:00",
            ),
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="b.py",
                content_hash="h-b2",
                priority=40,
                enqueue_source="l3",
                now_iso="2026-02-16T00:00:03+00:00",
            ),
            EnqueueRequestDTO(
                repo_id="",
                repo_root="/repo",
                relative_path="a.py",
                content_hash="h-a2",
                priority=30,
                enqueue_source="scan",
                now_iso="2026-02-16T00:00:04+00:00",
            ),
        ]
    )

    assert len(ids) == 4
    assert ids[1] == existing_id
    assert ids[3] == existing_id
    assert ids[0] == ids[2]
    assert ids[0] != existing_id

    row_a = _read_queue_row(db_path, existing_id)
    assert row_a["content_hash"] == "h-a2"
    assert row_a["next_retry_at"] == "2026-02-16T00:00:04+00:00"

    row_b = _read_queue_row(db_path, ids[0])
    assert row_b["content_hash"] == "h-b2"
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT priority, enqueue_source
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": ids[0]},
        ).fetchone()
    assert row is not None
    assert int(row["priority"]) == 40
    assert str(row["enqueue_source"]) == "l3"


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
    assert split["PENDING_DEFERRED_FAST"] == 0
    assert split["PENDING_DEFERRED_HEAVY"] == 0

    age = repo.get_pending_age_stats(now_iso="2026-02-16T00:00:00+00:00")
    assert age["oldest_pending_available_age_sec"] == pytest.approx(30.0)
    assert age["oldest_pending_deferred_age_sec"] == pytest.approx(600.0)
    assert age["p95_pending_available_age_sec"] is not None


def test_pending_split_counts_include_fast_and_heavy_defer_buckets(tmp_path: Path) -> None:
    """defer_reason prefix에 따라 fast/heavy 분리 카운트가 집계되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    base_now = "2026-02-16T00:00:00+00:00"
    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="fast.ts",
        content_hash="h-fast",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    _ = repo.enqueue(
        repo_root="/repo",
        relative_path="heavy.java",
        content_hash="h-heavy",
        priority=10,
        enqueue_source="scan",
        now_iso=base_now,
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET next_retry_at = '2026-02-16T00:00:15+00:00',
                defer_reason = 'l5_defer:tsls_fast:pressure_rate_exceeded'
            WHERE relative_path = 'fast.ts'
            """
        )
        conn.execute(
            """
            UPDATE file_enrich_queue
            SET next_retry_at = '2026-02-16T00:01:00+00:00',
                defer_reason = 'l5_defer:deferred_heavy:l3_preprocess_large_file'
            WHERE relative_path = 'heavy.java'
            """
        )
        conn.commit()

    split = repo.get_pending_split_counts(now_iso=base_now)

    assert split["PENDING_AVAILABLE"] == 0
    assert split["PENDING_DEFERRED"] == 2
    assert split["PENDING_DEFERRED_FAST"] == 1
    assert split["PENDING_DEFERRED_HEAVY"] == 1


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


def test_defer_jobs_to_pending_enforces_min_defer_seconds(tmp_path: Path) -> None:
    """min_defer_sec이 지정되면 즉시 재시도 시각은 최소 지연으로 보정되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="min_defer.py",
        content_hash="h-min-defer",
        priority=10,
        enqueue_source="scan",
        now_iso=now_iso,
    )
    _ = repo.acquire_pending(limit=1, now_iso=now_iso)

    changed = repo.defer_jobs_to_pending(
        job_ids=[job_id],
        next_retry_at=now_iso,
        defer_reason="broker_defer:budget",
        now_iso=now_iso,
        min_defer_sec=5,
    )
    assert changed == 1

    row = _read_queue_row(db_path, job_id)
    assert row["next_retry_at"] == "2026-02-16T00:00:05+00:00"


def test_escalate_scope_on_same_job_enforces_min_defer_seconds(tmp_path: Path) -> None:
    """scope escalation도 min_defer_sec을 적용해 즉시 재획득 루프를 방지해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)
    now_iso = "2026-02-16T00:00:00+00:00"
    job_id = repo.enqueue(
        repo_root="/repo",
        relative_path="scope_min_defer.py",
        content_hash="h-scope-min",
        priority=10,
        enqueue_source="scan",
        now_iso=now_iso,
    )

    changed = repo.escalate_scope_on_same_job(
        job_id=job_id,
        next_scope_level="repo",
        next_scope_root="/repo/subproj",
        next_retry_at=now_iso,
        now_iso=now_iso,
        min_defer_sec=5,
    )
    assert changed is True

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT next_retry_at
            FROM file_enrich_queue
            WHERE job_id = :job_id
            """,
            {"job_id": job_id},
        ).fetchone()
    assert row is not None
    assert str(row["next_retry_at"]) == "2026-02-16T00:00:05+00:00"
