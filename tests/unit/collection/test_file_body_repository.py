"""L2 본문 저장소 예외 정책을 검증한다."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from sari.core.models import CollectedFileBodyDTO, now_iso8601_utc
from sari.db.repositories.file_body_repository import FileBodyDecodeError, FileBodyRepository
from sari.db.schema import connect, init_schema


def test_read_body_text_raises_decode_error_for_corrupted_zlib(tmp_path: Path) -> None:
    """손상된 zlib 본문은 None으로 숨기지 않고 명시적 예외를 발생시켜야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileBodyRepository(db_path)

    now_iso = now_iso8601_utc()
    repo.upsert_body(
        CollectedFileBodyDTO(
            repo_id="r_repo",
            repo_root="/repo",
            relative_path="a.py",
            content_hash="hash-1",
            content_zlib=zlib.compress(b"print('ok')"),
            content_len=11,
            normalized_text="print('ok')",
            created_at=now_iso,
            updated_at=now_iso,
        )
    )

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE collected_file_bodies_l2
            SET content_zlib = :content
            WHERE repo_root = :repo_root
              AND relative_path = :relative_path
              AND content_hash = :content_hash
            """,
            {
                "content": b"not-zlib-body",
                "repo_root": "/repo",
                "relative_path": "a.py",
                "content_hash": "hash-1",
            },
        )
        conn.commit()

    with pytest.raises(FileBodyDecodeError):
        repo.read_body_text(repo_root="/repo", relative_path="a.py", content_hash="hash-1")
