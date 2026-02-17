"""DB row 엄격 매핑 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.core.exceptions import ValidationError
from sari.core.models import DaemonRuntimeDTO, now_iso8601_utc
from sari.db.repositories.file_body_repository import FileBodyDecodeError, FileBodyRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.row_mapper import row_bool, row_int, row_optional_str, row_str
from sari.db.schema import connect, init_schema


def test_row_mapper_helpers_raise_for_invalid_types() -> None:
    """row_mapper helper는 잘못된 타입에서 명시적으로 실패해야 한다."""
    fake_row = {
        "as_int": "1",
        "as_str": 123,
        "as_bool": "true",
        "as_opt": 99,
    }

    with pytest.raises(ValidationError) as exc_info:
        row_int(fake_row, "as_int")
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"

    with pytest.raises(ValidationError) as exc_info:
        row_str(fake_row, "as_str")
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"

    with pytest.raises(ValidationError) as exc_info:
        row_bool(fake_row, "as_bool")
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"

    with pytest.raises(ValidationError) as exc_info:
        row_optional_str(fake_row, "as_opt")
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"


def test_runtime_repository_raises_on_invalid_pid_type(tmp_path: Path) -> None:
    """runtime row 타입이 깨지면 RuntimeRepository가 명시적으로 실패해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = RuntimeRepository(db_path)
    now_iso = now_iso8601_utc()

    repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=1234,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at=now_iso,
            session_count=1,
            last_heartbeat_at=now_iso,
            last_exit_reason=None,
        )
    )

    with connect(db_path) as conn:
        conn.execute("UPDATE daemon_runtime SET pid = :pid WHERE singleton_key = 'default'", {"pid": "invalid"})
        conn.commit()

    with pytest.raises(ValidationError) as exc_info:
        repo.get_runtime()
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"


def test_symbol_cache_repository_raises_on_invalid_items_json_type(tmp_path: Path) -> None:
    """symbol cache row 타입이 깨지면 명시적 예외를 발생시켜야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = SymbolCacheRepository(db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO lsp_symbol_cache(repo_root, relative_path, query, file_hash, items_json, invalidated, updated_at)
            VALUES(:repo_root, :relative_path, :query, :file_hash, :items_json, 0, :updated_at)
            """,
            {
                "repo_root": "/repo",
                "relative_path": "a.py",
                "query": "alpha",
                "file_hash": "hash-1",
                "items_json": 123,
                "updated_at": now_iso8601_utc(),
            },
        )
        conn.commit()

    with pytest.raises(ValidationError) as exc_info:
        repo.get_cached_items(repo_root="/repo", relative_path="a.py", query="alpha", file_hash="hash-1")
    assert exc_info.value.context.code == "ERR_DB_MAPPING_INVALID"


def test_file_body_repository_raises_on_non_bytes_payload(tmp_path: Path) -> None:
    """file body payload가 bytes가 아니면 명시적 decode 오류를 발생시켜야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileBodyRepository(db_path)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_file_bodies_l2(
                repo_root, relative_path, content_hash, content_zlib, content_len,
                normalized_text, created_at, updated_at
            )
            VALUES(
                :repo_root, :relative_path, :content_hash, :content_zlib, :content_len,
                :normalized_text, :created_at, :updated_at
            )
            """,
            {
                "repo_root": "/repo",
                "relative_path": "bad.py",
                "content_hash": "hash-bad",
                "content_zlib": "not-bytes",
                "content_len": 9,
                "normalized_text": "not-bytes",
                "created_at": now_iso8601_utc(),
                "updated_at": now_iso8601_utc(),
            },
        )
        conn.commit()

    with pytest.raises(FileBodyDecodeError, match="content_zlib must be bytes"):
        repo.read_body_text(repo_root="/repo", relative_path="bad.py", content_hash="hash-bad")
