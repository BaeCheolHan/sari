"""후보 인덱스 변경 로그 저장소 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.repo.identity import compute_repo_id
from sari.core.models import CandidateIndexChangeDTO
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository, _extract_lastrowid
from sari.db.schema import connect, init_schema


def test_candidate_index_change_repository_coalesces_latest_upsert(tmp_path: Path) -> None:
    """동일 파일 upsert는 최신 1건으로 coalesce되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = CandidateIndexChangeRepository(db_path)
    first = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h1",
        mtime_ns=1,
        size_bytes=10,
        event_source="scan",
        recorded_at="2026-02-16T00:00:00+00:00",
    )
    second = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h2",
        mtime_ns=2,
        size_bytes=20,
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )

    repo.enqueue_upsert(first)
    repo.enqueue_upsert(second)
    items = repo.acquire_pending(limit=10)

    assert len(items) == 1
    assert items[0].change_type == "UPSERT"
    assert items[0].content_hash == "h2"
    assert items[0].event_source == "watcher"


def test_candidate_index_change_repository_delete_overwrites_pending_upsert(tmp_path: Path) -> None:
    """동일 파일에서 delete가 들어오면 pending upsert를 대체해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = CandidateIndexChangeRepository(db_path)
    dto = CandidateIndexChangeDTO(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        absolute_path="/repo-a/a.py",
        content_hash="h1",
        mtime_ns=1,
        size_bytes=10,
        event_source="scan",
        recorded_at="2026-02-16T00:00:00+00:00",
    )

    repo.enqueue_upsert(dto)
    repo.enqueue_delete(
        repo_id="r_repo_a",
        repo_root="/repo-a",
        relative_path="a.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )
    items = repo.acquire_pending(limit=10)

    assert len(items) == 1
    assert items[0].change_type == "DELETE"
    assert items[0].event_source == "watcher"


def test_extract_lastrowid_uses_fallback_query_when_raw_is_none() -> None:
    """raw lastrowid가 None이면 fallback query를 사용해야 한다."""

    class _Conn:
        def execute(self, query: str) -> object:
            _ = query
            return self

        def fetchone(self) -> dict[str, object]:
            return {"lastrowid": 7}

    resolved = _extract_lastrowid(conn=_Conn(), raw_lastrowid=None)
    assert resolved == 7


def test_extract_lastrowid_raises_when_both_raw_and_fallback_invalid() -> None:
    """raw/fallback 모두 무효하면 명시적 RuntimeError를 반환해야 한다."""

    class _Conn:
        def execute(self, query: str) -> object:
            _ = query
            return self

        def fetchone(self) -> dict[str, object]:
            return {"lastrowid": object()}

    try:
        _extract_lastrowid(conn=_Conn(), raw_lastrowid=None)
    except RuntimeError as exc:
        assert "failed to resolve last inserted change_id" in str(exc)
    else:
        raise AssertionError("RuntimeError was not raised")


def test_candidate_index_change_repository_resolves_repo_id_from_repositories_when_omitted(tmp_path: Path) -> None:
    """repo_id 미지정 변경 로그는 repositories SSOT의 repo_id를 사용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = str((tmp_path / "ws").resolve())
    repo_root = str((tmp_path / "ws" / "repo-a").resolve())
    Path(workspace_root).mkdir(parents=True, exist_ok=True)
    Path(repo_root).mkdir(parents=True, exist_ok=True)
    expected_repo_id = compute_repo_id(repo_label="repo-a", workspace_root=workspace_root)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspaces(path, name, indexed_at, is_active)
            VALUES(:path, 'ws', '2026-03-06T00:00:00Z', 1)
            """,
            {"path": workspace_root},
        )
        conn.execute(
            """
            INSERT INTO repositories(repo_id, repo_label, repo_root, workspace_root, updated_at, is_active)
            VALUES(:repo_id, 'repo-a', :repo_root, :workspace_root, '2026-03-06T00:00:00Z', 1)
            """,
            {"repo_id": expected_repo_id, "repo_root": repo_root, "workspace_root": workspace_root},
        )
        conn.commit()

    repo = CandidateIndexChangeRepository(db_path)
    repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="",
            repo_root=repo_root,
            relative_path="a.py",
            absolute_path=f"{repo_root}/a.py",
            content_hash="h1",
            mtime_ns=1,
            size_bytes=10,
            event_source="scan",
            recorded_at="2026-03-06T00:00:01Z",
        )
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT repo_id FROM candidate_index_changes WHERE repo_root = :repo_root AND relative_path = 'a.py'",
            {"repo_root": repo_root},
        ).fetchone()
    assert row is not None
    assert str(row["repo_id"]) == expected_repo_id
