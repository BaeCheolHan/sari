import sqlite3
from typing import List
from .base import BaseRepository

class SnippetRepository(BaseRepository):
    def upsert_snippet_tx(self, cur: sqlite3.Cursor, rows: List[tuple]) -> int:
        if not rows:
            return 0
        cur.executemany(
            """
            INSERT INTO snippets(path, tag, start_line, end_line, content, note, commit_sha, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        return len(rows)

class ContextRepository(BaseRepository):
    def upsert_context_tx(self, cur: sqlite3.Cursor, rows: List[tuple]) -> int:
        if not rows:
            return 0
        cur.executemany(
            """
            INSERT INTO domain_context(topic, content, tags, related_files, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(topic) DO UPDATE SET
              content=excluded.content,
              tags=excluded.tags,
              related_files=excluded.related_files,
              updated_at=excluded.updated_at
            """,
            rows,
        )
        return len(rows)
