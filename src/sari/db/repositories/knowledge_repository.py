"""스니펫/지식/문맥 저장소를 구현한다."""

from __future__ import annotations

import json
from pathlib import Path

from sari.core.exceptions import ErrorContext, ValidationError
from sari.core.models import KnowledgeEntryDTO, KnowledgeRecordDTO, SnippetRecordDTO, SnippetSaveDTO
from sari.db.row_mapper import row_int, row_optional_str, row_str
from sari.db.schema import connect


def _parse_string_tuple(raw_json: str, field_name: str) -> tuple[str, ...]:
    """JSON 배열 문자열을 문자열 튜플로 엄격 파싱한다."""
    try:
        loaded = json.loads(raw_json)
    except ValueError as exc:
        raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message=f"{field_name} json parse failed")) from exc
    if not isinstance(loaded, list):
        raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message=f"{field_name} must be json list"))
    parsed: list[str] = []
    for item in loaded:
        if not isinstance(item, str):
            raise ValidationError(ErrorContext(code="ERR_DB_MAPPING_INVALID", message=f"{field_name} item must be str"))
        parsed.append(item)
    return tuple(parsed)


class KnowledgeRepository:
    """레거시 지식/스니펫 도구 데이터 영속화를 담당한다."""

    def __init__(self, db_path: Path) -> None:
        """저장소에 사용할 DB 경로를 저장한다."""
        self._db_path = db_path

    def save_snippet(self, snippet: SnippetSaveDTO) -> int:
        """스니펫을 저장하고 생성된 식별자를 반환한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO snippet_entries(
                    repo_root, source_path, start_line, end_line, tag, note, commit_hash, content_text, created_at
                )
                VALUES(
                    :repo_root, :source_path, :start_line, :end_line, :tag, :note, :commit_hash, :content_text, :created_at
                )
                """,
                snippet.to_sql_params(),
            )
            conn.commit()
            last_row_id = int(cur.lastrowid if cur.lastrowid is not None else 0)
            return last_row_id

    def query_snippets(self, repo_root: str, tag: str | None, query: str | None, limit: int) -> list[SnippetRecordDTO]:
        """태그/질의 조건으로 스니펫을 조회한다."""
        where_clauses = ["repo_root = :repo_root"]
        params: dict[str, object] = {"repo_root": repo_root, "limit": limit}
        if tag is not None and tag.strip() != "":
            where_clauses.append("tag = :tag")
            params["tag"] = tag.strip()
        if query is not None and query.strip() != "":
            where_clauses.append("(content_text LIKE :query OR note LIKE :query OR source_path LIKE :query)")
            params["query"] = f"%{query.strip()}%"

        where_sql = " AND ".join(where_clauses)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT snippet_id, repo_root, source_path, start_line, end_line, tag, note, commit_hash, content_text, created_at
                FROM snippet_entries
                WHERE {where_sql}
                ORDER BY created_at DESC, snippet_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
        return [
            SnippetRecordDTO(
                snippet_id=row_int(row, "snippet_id"),
                repo_root=row_str(row, "repo_root"),
                source_path=row_str(row, "source_path"),
                start_line=row_int(row, "start_line"),
                end_line=row_int(row, "end_line"),
                tag=row_str(row, "tag"),
                note=row_optional_str(row, "note"),
                commit_hash=row_optional_str(row, "commit_hash"),
                content_text=row_str(row, "content_text"),
                created_at=row_str(row, "created_at"),
            )
            for row in rows
        ]

    def archive_knowledge(self, entry: KnowledgeEntryDTO) -> int:
        """지식/문맥 엔트리를 저장하고 생성된 식별자를 반환한다."""
        with connect(self._db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_entries(
                    kind, repo_root, topic, content_text, tags_json, related_files_json, created_at
                )
                VALUES(
                    :kind, :repo_root, :topic, :content_text, :tags_json, :related_files_json, :created_at
                )
                """,
                entry.to_sql_params(),
            )
            conn.commit()
            return int(cur.lastrowid if cur.lastrowid is not None else 0)

    def query_knowledge(self, repo_root: str, kind: str, query: str | None, limit: int) -> list[KnowledgeRecordDTO]:
        """지식/문맥 엔트리를 조회한다."""
        params: dict[str, object] = {"repo_root": repo_root, "kind": kind, "limit": limit}
        where_sql = "repo_root = :repo_root AND kind = :kind"
        if query is not None and query.strip() != "":
            params["query"] = f"%{query.strip()}%"
            where_sql = f"{where_sql} AND (topic LIKE :query OR content_text LIKE :query OR tags_json LIKE :query)"
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT entry_id, kind, repo_root, topic, content_text, tags_json, related_files_json, created_at
                FROM knowledge_entries
                WHERE {where_sql}
                ORDER BY created_at DESC, entry_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
        records: list[KnowledgeRecordDTO] = []
        for row in rows:
            records.append(
                KnowledgeRecordDTO(
                    entry_id=row_int(row, "entry_id"),
                    kind=row_str(row, "kind"),
                    repo_root=row_str(row, "repo_root"),
                    topic=row_str(row, "topic"),
                    content_text=row_str(row, "content_text"),
                    tags=_parse_string_tuple(row_str(row, "tags_json"), "tags_json"),
                    related_files=_parse_string_tuple(row_str(row, "related_files_json"), "related_files_json"),
                    created_at=row_str(row, "created_at"),
                )
            )
        return records
