import sqlite3
import logging
from typing import Any, Optional

logger = logging.getLogger("sari.repository.base")

class BaseRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        if conn and not conn.row_factory:
            try:
                conn.row_factory = sqlite3.Row
            except Exception as e:
                logger.error("Critical: Failed to set row_factory on DB connection: %s", e)
                # We don't raise here to avoid breaking existing flows, 
                # but it's a severe architectural failure.

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: Any = None) -> sqlite3.Cursor:
        try:
            if params:
                return self._conn.execute(sql, params)
            return self._conn.execute(sql)
        except Exception as e:
            logger.error("SQL Execution failed: %s\nSQL: %s\nParams: %s", e, sql, params)
            raise