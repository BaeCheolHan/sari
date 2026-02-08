import sqlite3
from typing import Any, Dict, Iterable, List, Optional
from .base import BaseRepository
from ..models import SnippetDTO, ContextDTO

class SnippetRepository(BaseRepository):
    def upsert_snippet_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = [list(r) for r in rows]
        if not rows_list:
            return 0
        normalized: List[tuple] = []
        for r in rows_list:
            while len(r) < 15:
                r.append("")
            if isinstance(r[2], (int, float)) and isinstance(r[3], (int, float)):
                # Tool format: (tag, path, start, end, content, content_hash, anchor_before, anchor_after, repo, root_id, note, commit, created_ts, updated_ts, metadata_json)
                mapped = (
                    str(r[0]),  # tag
                    str(r[1]),  # path
                    str(r[9]),  # root_id
                    int(r[2]),
                    int(r[3]),
                    str(r[4]),
                    str(r[5]),
                    str(r[6]),
                    str(r[7]),
                    str(r[8]),
                    str(r[10]),
                    str(r[11]),
                    int(r[12] or 0),
                    int(r[13] or 0),
                    str(r[14] or "{}"),
                )
            else:
                # Schema format: (tag, path, root_id, start_line, end_line, content, content_hash, anchor_before, anchor_after, repo, note, commit_hash, created_ts, updated_ts, metadata_json)
                mapped = (
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    int(r[3] or 0),
                    int(r[4] or 0),
                    str(r[5]),
                    str(r[6]),
                    str(r[7]),
                    str(r[8]),
                    str(r[9]),
                    str(r[10]),
                    str(r[11]),
                    int(r[12] or 0),
                    int(r[13] or 0),
                    str(r[14] or "{}"),
                )
            normalized.append(mapped)
        cur.executemany(
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
            normalized,
        )
        return len(normalized)

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

    def list_snippet_versions(self, snippet_id: int) -> List[Dict[str, Any]]:
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
    def upsert_context_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        rows_list = [list(r) for r in rows]
        if not rows_list:
            return 0
        normalized = []
        for r in rows_list:
            while len(r) < 10:
                r.append(0)
            normalized.append(
                (
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    str(r[3]),
                    str(r[4]),
                    int(r[5] or 0),
                    int(r[6] or 0),
                    int(r[7] or 0),
                    int(r[8] or 0),
                    int(r[9] or 0),
                )
            )
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

    def get_context_by_topic(self, topic: str) -> Optional[ContextDTO]:
        row = self.execute(
            """
            SELECT id, topic, content, tags_json, related_files_json, source, valid_from, valid_until, deprecated, created_ts, updated_ts
            FROM contexts WHERE topic = ?
            """,
            (topic,),
        ).fetchone()
        return ContextDTO.from_row(row) if row else None

    def search_contexts(self, query: str, limit: int = 20) -> List[ContextDTO]:
        if not query:
            return []
        lq = f"%{query}%"
        rows = self.execute(
            """
            SELECT id, topic, content, tags_json, related_files_json, source, valid_from, valid_until, deprecated, created_ts, updated_ts
            FROM contexts WHERE topic LIKE ? OR content LIKE ? OR tags_json LIKE ?
            ORDER BY updated_ts DESC LIMIT ?
            """,
            (lq, lq, lq, int(limit)),
        ).fetchall()
        return [ContextDTO.from_row(r) for r in rows]
