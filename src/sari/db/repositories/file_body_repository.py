"""L2 압축 본문 저장소를 구현한다."""

from __future__ import annotations

import zlib
from pathlib import Path

from sari.core.exceptions import ValidationError
from sari.core.text_decode import decode_bytes_with_policy
from sari.core.models import CollectedFileBodyDTO, FileBodyDeleteTargetDTO
from sari.db.row_mapper import row_bytes
from sari.db.schema import connect


class FileBodyDecodeError(Exception):
    """L2 본문 복원 실패를 나타낸다."""

    def __init__(self, repo_root: str, relative_path: str, content_hash: str, message: str) -> None:
        """파일 식별자와 오류 메시지를 저장한다."""
        super().__init__(message)
        self.repo_root = repo_root
        self.relative_path = relative_path
        self.content_hash = content_hash


class FileBodyRepository:
    """L2 압축 본문 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_body(self, body_row: CollectedFileBodyDTO) -> None:
        """L2 압축 본문 레코드를 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO collected_file_bodies_l2(
                    repo_root, scope_repo_root, relative_path, content_hash, content_zlib, content_len,
                    normalized_text, created_at, updated_at
                )
                VALUES(
                    :repo_root, :scope_repo_root, :relative_path, :content_hash, :content_zlib, :content_len,
                    :normalized_text, :created_at, :updated_at
                )
                ON CONFLICT(repo_root, relative_path, content_hash) DO UPDATE SET
                    scope_repo_root = excluded.scope_repo_root,
                    content_zlib = excluded.content_zlib,
                    content_len = excluded.content_len,
                    normalized_text = excluded.normalized_text,
                    updated_at = excluded.updated_at
                """,
                body_row.to_sql_params(),
            )
            conn.commit()

    def upsert_body_many(self, body_rows: list[CollectedFileBodyDTO]) -> None:
        """L2 압축 본문 레코드를 배치 업서트한다."""
        if len(body_rows) == 0:
            return
        with connect(self._db_path) as conn:
            for body_row in body_rows:
                conn.execute(
                    """
                    INSERT INTO collected_file_bodies_l2(
                        repo_root, scope_repo_root, relative_path, content_hash, content_zlib, content_len,
                        normalized_text, created_at, updated_at
                    )
                    VALUES(
                        :repo_root, :scope_repo_root, :relative_path, :content_hash, :content_zlib, :content_len,
                        :normalized_text, :created_at, :updated_at
                    )
                    ON CONFLICT(repo_root, relative_path, content_hash) DO UPDATE SET
                        scope_repo_root = excluded.scope_repo_root,
                        content_zlib = excluded.content_zlib,
                        content_len = excluded.content_len,
                        normalized_text = excluded.normalized_text,
                        updated_at = excluded.updated_at
                    """,
                    body_row.to_sql_params(),
                )
            conn.commit()

    def read_body_text(self, repo_root: str, relative_path: str, content_hash: str) -> str | None:
        """압축 본문을 복원하여 텍스트를 반환한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT content_zlib
                FROM collected_file_bodies_l2
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            ).fetchone()
        if row is None:
            return None
        try:
            compressed = row_bytes(row, "content_zlib")
        except ValidationError as exc:
            raise FileBodyDecodeError(
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
                message=str(exc),
            ) from exc
        try:
            raw = zlib.decompress(compressed)
        except zlib.error as exc:
            raise FileBodyDecodeError(
                repo_root=repo_root,
                relative_path=relative_path,
                content_hash=content_hash,
                message=f"압축 본문 복원 실패: {exc}",
            ) from exc
        return decode_bytes_with_policy(raw).text

    def delete_body(self, repo_root: str, relative_path: str, content_hash: str) -> None:
        """L2 압축 본문 레코드를 삭제한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                DELETE FROM collected_file_bodies_l2
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                  AND content_hash = :content_hash
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "content_hash": content_hash,
                },
            )
            conn.commit()

    def delete_body_many(self, targets: list[FileBodyDeleteTargetDTO]) -> None:
        """L2 압축 본문 레코드를 배치 삭제한다."""
        if len(targets) == 0:
            return
        with connect(self._db_path) as conn:
            for target in targets:
                conn.execute(
                    """
                    DELETE FROM collected_file_bodies_l2
                    WHERE repo_root = :repo_root
                      AND relative_path = :relative_path
                      AND content_hash = :content_hash
                    """,
                    target.to_sql_params(),
                )
            conn.commit()
