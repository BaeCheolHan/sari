import sqlite3
import logging
from collections.abc import Mapping, Sequence
from typing import TypeAlias

logger = logging.getLogger("sari.repository.base")

SqlParams: TypeAlias = Sequence[object] | Mapping[str, object]

class BaseRepository:
    """
    Sari 저장소의 기반 클래스로, SQLite 데이터베이스 연결 및 공통 실행 로직을 관리합니다.
    모든 데이터 접근 객체(DAO)는 이 클래스를 상속받아 구현됩니다.
    """
    def __init__(self, conn: sqlite3.Connection):
        """
        주어진 SQLite 연결 객체를 초기화하고, Row 결과에 쉽게 접근할 수 있도록 row_factory를 설정합니다.
        """
        self._conn = conn
        if conn and not conn.row_factory:
            try:
                conn.row_factory = sqlite3.Row
            except Exception as e:
                logger.error("Critical: Failed to set row_factory on DB connection: %s", e)
                # 예외를 던지지 않고 로그만 남겨 기존 흐름을 유지하되, 이는 심각한 설계적 결함입니다.

    @property
    def connection(self) -> sqlite3.Connection:
        """현재 리포지토리가 사용하는 DB 연결 객체를 반환합니다."""
        return self._conn

    def execute(self, sql: str, params: SqlParams | None = None) -> sqlite3.Cursor:
        """
        SQL 쿼리를 실행하고 결과를 Cursor로 반환합니다.
        실패 시 상세 로그를 남기고 예외를 다시 던집니다.
        """
        try:
            if params:
                return self._conn.execute(sql, params)
            return self._conn.execute(sql)
        except Exception as e:
            logger.error("SQL Execution failed: %s\nSQL: %s\nParams: %s", e, sql, params)
            raise
