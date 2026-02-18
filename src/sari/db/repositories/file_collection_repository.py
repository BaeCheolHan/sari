"""파일 수집 L1 저장소를 구현한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectedFileL1DTO, EnrichStateUpdateDTO, FileListItemDTO
from sari.db.row_mapper import row_bool, row_int, row_str
from sari.db.schema import connect


class FileCollectionRepository:
    """L1 파일 메타데이터 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def upsert_file(self, file_row: CollectedFileL1DTO) -> None:
        """L1 파일 메타데이터를 업서트한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO collected_files_l1(
                    repo_root, relative_path, absolute_path, repo_label, mtime_ns, size_bytes,
                    content_hash, is_deleted, last_seen_at, updated_at, enrich_state
                )
                VALUES(
                    :repo_root, :relative_path, :absolute_path, :repo_label, :mtime_ns, :size_bytes,
                    :content_hash, :is_deleted, :last_seen_at, :updated_at, :enrich_state
                )
                ON CONFLICT(repo_root, relative_path) DO UPDATE SET
                    absolute_path = excluded.absolute_path,
                    repo_label = excluded.repo_label,
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    content_hash = excluded.content_hash,
                    is_deleted = excluded.is_deleted,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at,
                    enrich_state = excluded.enrich_state
                """,
                file_row.to_sql_params(),
            )
            conn.commit()

    def upsert_files_many(self, file_rows: list[CollectedFileL1DTO]) -> None:
        """L1 파일 메타데이터를 배치 업서트한다."""
        if len(file_rows) == 0:
            return
        with connect(self._db_path) as conn:
            for file_row in file_rows:
                conn.execute(
                    """
                    INSERT INTO collected_files_l1(
                        repo_root, relative_path, absolute_path, repo_label, mtime_ns, size_bytes,
                        content_hash, is_deleted, last_seen_at, updated_at, enrich_state
                    )
                    VALUES(
                        :repo_root, :relative_path, :absolute_path, :repo_label, :mtime_ns, :size_bytes,
                        :content_hash, :is_deleted, :last_seen_at, :updated_at, :enrich_state
                    )
                    ON CONFLICT(repo_root, relative_path) DO UPDATE SET
                        absolute_path = excluded.absolute_path,
                        repo_label = excluded.repo_label,
                        mtime_ns = excluded.mtime_ns,
                        size_bytes = excluded.size_bytes,
                        content_hash = excluded.content_hash,
                        is_deleted = excluded.is_deleted,
                        last_seen_at = excluded.last_seen_at,
                        updated_at = excluded.updated_at,
                        enrich_state = excluded.enrich_state
                    """,
                    file_row.to_sql_params(),
                )
            conn.commit()

    def sync_repo_label(self, repo_root: str, repo_label: str) -> int:
        """특정 저장소의 기존 행 repo_label을 현재 정책 값으로 동기화한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE collected_files_l1
                SET repo_label = :repo_label
                WHERE repo_root = :repo_root
                  AND (repo_label IS NULL OR repo_label = '' OR repo_label != :repo_label)
                """,
                {"repo_root": repo_root, "repo_label": repo_label},
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def list_files(self, repo_root: str, limit: int, prefix: str | None = None) -> list[FileListItemDTO]:
        """활성 파일 목록을 조회한다."""
        where_prefix = ""
        params: dict[str, object] = {"repo_root": repo_root, "limit": limit}
        if prefix is not None and prefix.strip() != "":
            where_prefix = "AND relative_path LIKE :prefix"
            params["prefix"] = f"{prefix.strip()}%"

        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT repo_root, relative_path, size_bytes, mtime_ns, content_hash, enrich_state
                FROM collected_files_l1
                WHERE repo_root = :repo_root
                  AND is_deleted = 0
                  {where_prefix}
                ORDER BY relative_path ASC
                LIMIT :limit
                """,
                params,
            ).fetchall()

        items: list[FileListItemDTO] = []
        for row in rows:
            items.append(
                FileListItemDTO(
                    repo=row_str(row, "repo_root"),
                    relative_path=row_str(row, "relative_path"),
                    size_bytes=row_int(row, "size_bytes"),
                    mtime_ns=row_int(row, "mtime_ns"),
                    content_hash=row_str(row, "content_hash"),
                    enrich_state=row_str(row, "enrich_state"),
                )
            )
        return items

    def get_file(self, repo_root: str, relative_path: str) -> CollectedFileL1DTO | None:
        """단일 파일 메타데이터를 조회한다."""
        with connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT repo_root, relative_path, absolute_path, repo_label, mtime_ns, size_bytes, content_hash,
                       is_deleted, last_seen_at, updated_at, enrich_state
                FROM collected_files_l1
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {"repo_root": repo_root, "relative_path": relative_path},
            ).fetchone()
        if row is None:
            return None
        return CollectedFileL1DTO(
            repo_root=row_str(row, "repo_root"),
            relative_path=row_str(row, "relative_path"),
            absolute_path=row_str(row, "absolute_path"),
            repo_label=row_str(row, "repo_label"),
            mtime_ns=row_int(row, "mtime_ns"),
            size_bytes=row_int(row, "size_bytes"),
            content_hash=row_str(row, "content_hash"),
            is_deleted=row_bool(row, "is_deleted"),
            last_seen_at=row_str(row, "last_seen_at"),
            updated_at=row_str(row, "updated_at"),
            enrich_state=row_str(row, "enrich_state"),
        )

    def mark_missing_as_deleted(self, repo_root: str, seen_relative_paths: list[str], updated_at: str, scan_started_at: str) -> int:
        """스캔 시작 시각 이전 last_seen 항목 중 누락 파일을 삭제 상태로 전환한다."""
        with connect(self._db_path) as conn:
            if len(seen_relative_paths) == 0:
                cur = conn.execute(
                    """
                    UPDATE collected_files_l1
                    SET is_deleted = 1,
                        updated_at = :updated_at,
                        enrich_state = 'DELETED'
                    WHERE repo_root = :repo_root
                      AND is_deleted = 0
                      AND last_seen_at < :scan_started_at
                    """,
                    {"repo_root": repo_root, "updated_at": updated_at, "scan_started_at": scan_started_at},
                )
                conn.commit()
                return int(cur.rowcount if cur.rowcount is not None else 0)

            placeholders: list[str] = []
            params: dict[str, object] = {"repo_root": repo_root, "updated_at": updated_at, "scan_started_at": scan_started_at}
            for index, value in enumerate(seen_relative_paths):
                key = f"seen_{index}"
                placeholders.append(f":{key}")
                params[key] = value

            cur = conn.execute(
                f"""
                UPDATE collected_files_l1
                SET is_deleted = 1,
                    updated_at = :updated_at,
                    enrich_state = 'DELETED'
                WHERE repo_root = :repo_root
                  AND is_deleted = 0
                  AND last_seen_at < :scan_started_at
                  AND relative_path NOT IN ({", ".join(placeholders)})
                """,
                params,
            )
            conn.commit()
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def update_enrich_state(self, repo_root: str, relative_path: str, enrich_state: str, updated_at: str) -> None:
        """파일 보강 상태를 갱신한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE collected_files_l1
                SET enrich_state = :enrich_state,
                    updated_at = :updated_at
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "enrich_state": enrich_state,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def update_enrich_state_many(self, updates: list[EnrichStateUpdateDTO]) -> None:
        """파일 보강 상태를 배치로 갱신한다."""
        if len(updates) == 0:
            return
        with connect(self._db_path) as conn:
            for item in updates:
                conn.execute(
                    """
                    UPDATE collected_files_l1
                    SET enrich_state = :enrich_state,
                        updated_at = :updated_at
                    WHERE repo_root = :repo_root
                      AND relative_path = :relative_path
                    """,
                    item.to_sql_params(),
                )
            conn.commit()

    def mark_deleted(self, repo_root: str, relative_path: str, updated_at: str) -> None:
        """특정 파일을 삭제 상태로 전환한다."""
        with connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE collected_files_l1
                SET is_deleted = 1,
                    updated_at = :updated_at,
                    enrich_state = 'DELETED'
                WHERE repo_root = :repo_root
                  AND relative_path = :relative_path
                """,
                {
                    "repo_root": repo_root,
                    "relative_path": relative_path,
                    "updated_at": updated_at,
                },
            )
            conn.commit()

    def get_repo_stats(self) -> list[dict[str, object]]:
        """저장소별 파일 수 통계를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT repo_root, COUNT(relative_path) AS file_count
                FROM collected_files_l1
                WHERE is_deleted = 0
                GROUP BY repo_root
                ORDER BY repo_root ASC
                """
            ).fetchall()
        stats: list[dict[str, object]] = []
        for row in rows:
            stats.append({"repo": row_str(row, "repo_root"), "file_count": row_int(row, "file_count")})
        return stats

    def get_enrich_state_counts(self) -> dict[str, int]:
        """활성 파일의 enrich_state 분포를 조회한다."""
        with connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT enrich_state, COUNT(relative_path) AS file_count
                FROM collected_files_l1
                WHERE is_deleted = 0
                GROUP BY enrich_state
                """
            ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row_str(row, "enrich_state")] = row_int(row, "file_count")
        return counts
