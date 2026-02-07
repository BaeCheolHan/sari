import sqlite3
from typing import Any, Optional

class BaseRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: Any = None) -> sqlite3.Cursor:
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)
