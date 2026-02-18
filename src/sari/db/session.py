"""SQLAlchemy 세션 팩토리 유틸리티를 제공한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from contextlib import AbstractContextManager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy import create_engine


class SessionContextFactory(Protocol):
    """세션 컨텍스트 팩토리 프로토콜을 정의한다."""

    def __call__(self) -> AbstractContextManager[Session]:
        """`with` 구문에서 사용할 세션 컨텍스트를 반환한다."""


@lru_cache(maxsize=8)
def _cached_engine(db_path_text: str) -> Engine:
    """DB 경로별 SQLAlchemy 엔진을 캐시한다."""
    engine = create_engine(f"sqlite:///{db_path_text}", future=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: object, connection_record: object) -> None:
        """SQLite 연결 직후 공통 PRAGMA를 적용한다."""
        _ = connection_record
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def build_session_factory(db_path: Path) -> Callable[[], AbstractContextManager[Session]]:
    """DB 경로 기반 세션 컨텍스트 팩토리를 생성한다."""
    db_path_text = str(db_path.expanduser().resolve())
    engine = _cached_engine(db_path_text)

    @contextmanager
    def _session_scope() -> AbstractContextManager[Session]:
        with Session(engine, future=True) as session:
            yield session

    return _session_scope


def resolve_session_factory(
    db_path: Path,
    session_factory: Callable[[], AbstractContextManager[Session]] | None,
) -> Callable[[], AbstractContextManager[Session]]:
    """주입값이 없으면 기본 세션 팩토리를 사용한다."""
    if session_factory is not None:
        return session_factory
    return build_session_factory(db_path)
