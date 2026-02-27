"""FileCollectionRepository 배치 처리 계약을 검증한다."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sari.core.models import CollectedFileL1DTO
import sari.db.repositories.file_collection_repository as file_collection_repository_module
from sari.db.repositories.file_collection_repository import FileCollectionRepository


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, object]] = []
        self.executemany_calls: list[tuple[str, list[dict[str, object]]]] = []
        self.committed = False

    def execute(self, sql: str, params: object = None) -> None:
        self.execute_calls.append((sql, params))

    def executemany(self, sql: str, seq_of_params) -> None:  # noqa: ANN001
        self.executemany_calls.append((sql, list(seq_of_params)))

    def commit(self) -> None:
        self.committed = True


def _row(relative_path: str, content_hash: str) -> CollectedFileL1DTO:
    return CollectedFileL1DTO(
        repo_id="r_repo",
        repo_root="/repo",
        scope_repo_root="/repo",
        relative_path=relative_path,
        absolute_path=f"/repo/{relative_path}",
        repo_label="repo",
        mtime_ns=1,
        size_bytes=1,
        content_hash=content_hash,
        is_deleted=False,
        last_seen_at="2026-02-16T00:00:00+00:00",
        updated_at="2026-02-16T00:00:00+00:00",
        enrich_state="PENDING",
    )


def test_upsert_files_many_uses_executemany(monkeypatch) -> None:
    """배치 업서트는 루프 execute 대신 executemany 경로를 사용해야 한다."""
    fake_conn = _FakeConnection()

    @contextmanager
    def _fake_connect(_: Path):
        yield fake_conn

    monkeypatch.setattr(file_collection_repository_module, "connect", _fake_connect)
    repo = FileCollectionRepository(Path("/tmp/state.db"))

    repo.upsert_files_many([_row("a.py", "h1"), _row("b.py", "h2")])

    assert len(fake_conn.executemany_calls) == 1
    assert len(fake_conn.execute_calls) == 0
    assert fake_conn.committed is True
    _, params = fake_conn.executemany_calls[0]
    assert len(params) == 2
    assert params[0]["relative_path"] == "a.py"
    assert params[1]["relative_path"] == "b.py"
