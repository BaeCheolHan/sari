import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional, Sequence
from .base import BaseRepository
from ..models import SnippetDTO, ContextDTO


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return int(default)


def _row_to_named_dict(row: object, columns: List[str], fill: object = "") -> Dict[str, object]:
    if isinstance(row, Mapping):
        return {col: row.get(col, fill) for col in columns}
    values = list(row) if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) else []
    padded = values + [fill] * max(0, len(columns) - len(values))
    return dict(zip(columns, padded, strict=False))


class SnippetRepository(BaseRepository):
    """
    코드 스니펫(Snippet) 정보를 관리하는 저장소입니다.
    사용자가 저장한 코드 조각의 위치, 내용, 태그 및 버전을 SQLite 'snippets' 테이블에 저장합니다.
    """
    def upsert_snippet_tx(self, cur: sqlite3.Cursor, rows: Iterable[object]) -> int:
        """스니펫 정보들을 트랜잭션 내에서 삽입하거나 업데이트합니다."""
        rows_list = list(rows)
        if not rows_list:
            return 0
        
        count = 0
        for r in rows_list:
            norm = self._normalize_snippet_row(r)
            # tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json
            tag, path, root_id, start, end, content, c_hash, a_before, a_after, repo, note, commit, c_ts, u_ts, meta = norm
            
            # 1. Try to find an existing snippet that is safely identifiable as the same snippet.
            # Match by stable content hash first, or by both anchors when available.
            existing = cur.execute(
                """
                SELECT id, content_hash, anchor_before, anchor_after
                FROM snippets
                WHERE tag = ? AND path = ? AND root_id = ?
                  AND (
                    content_hash = ?
                    OR (
                      anchor_before = ? AND anchor_after = ?
                      AND anchor_before != '' AND anchor_after != ''
                    )
                  )
                ORDER BY updated_ts DESC
                LIMIT 1
                """,
                (tag, path, root_id, c_hash, a_before, a_after),
            ).fetchone()
            
            if existing:
                eid, e_hash, e_before, e_after = existing
                is_same = (e_hash == c_hash) or (
                    bool(a_before) and bool(a_after) and
                    e_before == a_before and e_after == a_after
                )
                
                if is_same:
                    cur.execute(
                        """
                        UPDATE snippets SET
                          start_line=?, end_line=?, content=?, content_hash=?, anchor_before=?, anchor_after=?,
                          repo=?, note=?, commit_hash=?, updated_ts=?, metadata_json=?
                        WHERE id = ?
                        """,
                        (start, end, content, c_hash, a_before, a_after, repo, note, commit, u_ts, meta, eid)
                    )
                    count += 1
                    continue

            # 2. Fallback to standard insert (or replace if exact conflict)
            cur.execute(
                """
                INSERT INTO snippets(tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tag, root_id, path, start_line, end_line) DO UPDATE SET
                  content=excluded.content,
                  content_hash=excluded.content_hash,
                  anchor_before=excluded.anchor_before,
                  anchor_after=excluded.anchor_after,
                  repo=excluded.repo,
                  note=excluded.note,
                  commit_hash=excluded.commit_hash,
                  updated_ts=excluded.updated_ts,
                  metadata_json=excluded.metadata_json;
                """,
                norm,
            )
            count += 1
            
        return count

    def _normalize_snippet_row(self, row: object) -> tuple:
        tool_cols = [
            "tag",
            "path",
            "start",
            "end",
            "content",
            "content_hash",
            "anchor_before",
            "anchor_after",
            "repo",
            "root_id",
            "note",
            "commit_hash",
            "created_ts",
            "updated_ts",
            "metadata_json",
        ]
        schema_cols = [
            "tag",
            "path",
            "root_id",
            "start_line",
            "end_line",
            "content",
            "content_hash",
            "anchor_before",
            "anchor_after",
            "repo",
            "note",
            "commit_hash",
            "created_ts",
            "updated_ts",
            "metadata_json",
        ]
        tool_data = _row_to_named_dict(row, tool_cols, fill="")
        schema_data = _row_to_named_dict(row, schema_cols, fill="")

        if isinstance(tool_data.get("start"), (int, float)) and isinstance(tool_data.get("end"), (int, float)):
            # Tool format:
            # (tag, path, start, end, content, content_hash, anchor_before, anchor_after, repo, root_id, note, commit, created_ts, updated_ts, metadata_json)
            source = tool_data
            return (
                str(source.get("tag", "")),
                str(source.get("path", "")),
                str(source.get("root_id", "")),
                _coerce_int(source.get("start")),
                _coerce_int(source.get("end")),
                str(source.get("content", "")),
                str(source.get("content_hash", "")),
                str(source.get("anchor_before", "")),
                str(source.get("anchor_after", "")),
                str(source.get("repo", "")),
                str(source.get("note", "")),
                str(source.get("commit_hash", "")),
                _coerce_int(source.get("created_ts")),
                _coerce_int(source.get("updated_ts")),
                str(source.get("metadata_json") or "{}"),
            )
        # Schema format:
        # (tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json)
        source = schema_data
        return (
            str(source.get("tag", "")),
            str(source.get("path", "")),
            str(source.get("root_id", "")),
            _coerce_int(source.get("start_line")),
            _coerce_int(source.get("end_line")),
            str(source.get("content", "")),
            str(source.get("content_hash", "")),
            str(source.get("anchor_before", "")),
            str(source.get("anchor_after", "")),
            str(source.get("repo", "")),
            str(source.get("note", "")),
            str(source.get("commit_hash", "")),
            _coerce_int(source.get("created_ts")),
            _coerce_int(source.get("updated_ts")),
            str(source.get("metadata_json") or "{}"),
        )

    def update_snippet_location_tx(
        self,
        cur: sqlite3.Cursor,
        snippet_id: int,
        start: int,
        end: int,
        content: str,
        content_hash: str,
        anchor_before: str,
        anchor_after: str,
        updated_ts: int,
    ) -> None:
        cur.execute(
            """
            UPDATE snippets
            SET start_line = ?, end_line = ?, content = ?, content_hash = ?, anchor_before = ?, anchor_after = ?, updated_ts = ?
            WHERE id = ?
            """,
            (int(start), int(end), str(content), str(content_hash), str(anchor_before), str(anchor_after), int(updated_ts), int(snippet_id)),
        )

    def list_snippet_versions(self, snippet_id: int) -> List[Dict[str, object]]:
        # This keeps raw dict for internal version history, but we could DTO-ize if needed
        rows = self.execute(
            "SELECT id, content, content_hash, created_ts FROM snippet_versions WHERE snippet_id = ? ORDER BY created_ts DESC",
            (int(snippet_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_snippets_by_tag(self, tag: str, limit: int = 20) -> List[SnippetDTO]:
        rows = self.execute(
            """
            SELECT id, tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json
            FROM snippets WHERE tag = ? ORDER BY updated_ts DESC LIMIT ?
            """,
            (tag, int(limit)),
        ).fetchall()
        return [SnippetDTO.from_row(r) for r in rows]

    def search_snippets(self, query: str, limit: int = 20) -> List[SnippetDTO]:
        if not query:
            return []
        lq = f"%{query}%"
        rows = self.execute(
            """
            SELECT id, tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json
            FROM snippets WHERE tag LIKE ? OR path LIKE ? OR content LIKE ? OR note LIKE ?
            ORDER BY updated_ts DESC LIMIT ?
            """,
            (lq, lq, lq, lq, int(limit)),
        ).fetchall()
        return [SnippetDTO.from_row(r) for r in rows]


class ContextRepository(BaseRepository):
    """
    인덱싱이나 분석 시 사용되는 맥락(Context) 정보를 관리하는 저장소입니다.
    특정 주제(Topic)에 대한 설명, 관련 파일, 태그 및 유효 기간 정보를 SQLite 'contexts' 테이블에 저장합니다.
    """
    def upsert(self, data: object) -> ContextDTO:
        """단일 맥락 정보를 삽입하거나 업데이트하고 DTO 객체를 반환합니다."""
        from ..models import ContextDTO
        import time
        import json
        
        if isinstance(data, ContextDTO):
            obj = data
        else:
            if not isinstance(data, Mapping):
                raise TypeError("Context upsert data must be ContextDTO or mapping")
            obj = ContextDTO(**data)
            
        now = int(time.time())
        row = (
            obj.topic, obj.content, 
            json.dumps(obj.tags, ensure_ascii=False), json.dumps(obj.related_files, ensure_ascii=False),
            obj.source, obj.valid_from, obj.valid_until,
            1 if obj.deprecated else 0,
            obj.created_ts or now, now
        )
        
        cur = self.connection.cursor()
        self.upsert_context_tx(cur, [row])
        self.connection.commit()
        return obj

    def upsert_context_tx(self, cur: sqlite3.Cursor, rows: Iterable[object]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        normalized = [self._normalize_context_row(r) for r in rows_list]
        cur.executemany(
            """
            INSERT INTO contexts(topic, content, tags_json, related_files_json, source, valid_from, valid_until, deprecated, created_ts, updated_ts)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(topic) DO UPDATE SET
              content=excluded.content,
              tags_json=excluded.tags_json,
              related_files_json=excluded.related_files_json,
              source=excluded.source,
              valid_from=excluded.valid_from,
              valid_until=excluded.valid_until,
              deprecated=excluded.deprecated,
              updated_ts=excluded.updated_ts;
            """,
            normalized,
        )
        return len(normalized)

    def _normalize_context_row(self, row: object) -> tuple:
        data = _row_to_named_dict(
            row,
            [
                "topic",
                "content",
                "tags_json",
                "related_files_json",
                "source",
                "valid_from",
                "valid_until",
                "deprecated",
                "created_ts",
                "updated_ts",
            ],
            fill=0,
        )
        return (
            str(data.get("topic", "")),
            str(data.get("content", "")),
            str(data.get("tags_json", "")),
            str(data.get("related_files_json", "")),
            str(data.get("source", "")),
            _coerce_int(data.get("valid_from")),
            _coerce_int(data.get("valid_until")),
            _coerce_int(data.get("deprecated")),
            _coerce_int(data.get("created_ts")),
            _coerce_int(data.get("updated_ts")),
        )

    def get_context_by_topic(self, topic: str, as_of: int = 0) -> Optional[ContextDTO]:
        """주제(Topic) 이름을 기준으로 유효한 맥락 정보를 조회합니다."""
        sql = """
            SELECT id, topic, content, tags_json, related_files_json, source, valid_from, valid_until, deprecated, created_ts, updated_ts
            FROM contexts WHERE topic = ?
        """
        params = [topic]
        if as_of:
            sql += " AND deprecated = 0 AND (valid_from = 0 OR valid_from <= ?) AND (valid_until = 0 OR valid_until >= ?)"
            params.extend([as_of, as_of])
            
        row = self.execute(sql, params).fetchone()
        return ContextDTO.from_row(row) if row else None

    def search_contexts(self, query: str, limit: int = 20, as_of: int = 0) -> List[ContextDTO]:
        if not query:
            return []
        lq = f"%{query}%"
        # Use JSON-aware searching for tags to avoid matching JSON structural characters
        sql = """
            SELECT id, topic, content, tags_json, related_files_json, source, valid_from, valid_until, deprecated, created_ts, updated_ts
            FROM contexts 
            WHERE (topic LIKE ? OR content LIKE ? OR EXISTS (
                SELECT 1 FROM json_each(contexts.tags_json) WHERE LOWER(value) = LOWER(?)
            ))
        """
        params = [lq, lq, query]
        if as_of:
            sql += " AND deprecated = 0 AND (valid_from = 0 OR valid_from <= ?) AND (valid_until = 0 OR valid_until >= ?)"
            params.extend([as_of, as_of])
            
        sql += " ORDER BY updated_ts DESC LIMIT ?"
        params.append(int(limit))
        
        rows = self.execute(sql, params).fetchall()
        return [ContextDTO.from_row(r) for r in rows]
