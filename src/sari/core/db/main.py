import sqlite3
import time
import logging
import os
import re
import threading
from typing import Dict, Iterable, List, Optional, Tuple
from peewee import SqliteDatabase
from .models import db_proxy
from .query_utils import apply_root_filter as _apply_root_filter_impl
from .row_codec import (
    decode_file_content,
    normalize_repo_stat_row,
    normalize_root_row,
    normalize_search_row,
    row_content_value,
)
from ..models import ContextDTO, FILE_COLUMNS, SearchOptions, SnippetDTO
from .schema import init_schema
from ..utils.path import PathUtils

logger = logging.getLogger("sari.db")


class LocalSearchDB:
    """
    Sari의 로컬 SQLite 데이터베이스를 관리하는 핵심 클래스입니다.
    파일 인덱스, 심볼, 메타데이터 등을 저장하고 검색하는 기능을 제공합니다.
    Peewee ORM을 기반으로 하며, 성능을 위해 직접 SQL을 실행하거나 메모리 스테이징을 사용하기도 합니다.
    """

    def __init__(self,
                 db_path: str,
                 logger_obj: Optional[logging.Logger] = None,
                 bind_proxy: bool = True,
                 **kwargs):
        """
        Args:
            db_path: SQLite 데이터베이스 파일 경로
            logger_obj: 로거 객체 (기본값: sari.db)
            bind_proxy: 전역 db_proxy를 이 DB로 초기화할지 여부
            kwargs: 추가 DB 설정 (journal_mode 등)
        """
        self.db_path = db_path
        self.logger = logger_obj or logger
        self.engine = None
        self._lock = threading.Lock()
        self._writer_thread_id: Optional[int] = None
        try:
            # SQLite 최적화 설정
            self.db = SqliteDatabase(db_path, pragmas={
                # WAL 모드 (동시성 향상)
                'journal_mode': kwargs.get('journal_mode', 'wal'),
                'cache_size': -10000,  # 약 10MB 페이지 캐시
                'foreign_keys': 1,    # 외래 키 제약 조건 활성화
                'ignore_check_constraints': 0,
                'synchronous': 1,     # NORMAL (안전성과 성능의 균형)
                'busy_timeout': 15000  # Lock 대기 시간
            })
            if bind_proxy:
                db_proxy.initialize(self.db)
            self.db.connect()
            init_schema(self.db.connection())
            self._init_mem_staging()
        except Exception as e:
            self.logger.error(
                "Failed to initialize database: %s",
                e,
                exc_info=True)
            raise

    def _init_mem_staging(self, force=True):
        """
        대량 삽입 성능을 위해 인메모리 스테이징(staging) 테이블을 초기화합니다.
        메인 디스크 DB에 쓰기 전, 메모리 DB(Attached DB)에 먼저 기록합니다.
        """
        with self._lock:
            try:
                conn = self.db.connection()
                dbs = [name for _, name, *_ in conn.execute("PRAGMA database_list").fetchall()]
                if "staging_mem" not in dbs:
                    conn.execute("ATTACH DATABASE ':memory:' AS staging_mem")
                if force:
                    conn.execute("DROP TABLE IF EXISTS staging_mem.files_temp")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS staging_mem.files_temp AS SELECT * FROM main.files WHERE 0")
            except Exception as e:
                self.logger.error(
                    "Failed to init mem staging: %s", e, exc_info=True)
                raise

    def _ensure_staging(self):
        try:
            self._init_mem_staging(force=False)
        except Exception:
            self._init_mem_staging(force=True)

    def create_staging_table(self, cur=None):
        self._init_mem_staging(force=True)

    def upsert_root(
            self,
            root_id: str,
            root_path: str,
            real_path: str,
            **kwargs):
        """워크스페이스 루트 정보를 갱신하거나 삽입합니다."""
        now = int(time.time())
        label = kwargs.get("label", PathUtils.normalize(root_path).split("/")[-1])
        n_root_path = PathUtils.normalize(root_path)
        n_real_path = PathUtils.normalize(real_path)
        # bind_proxy=False 환경(스냅샷 워커)에서도 동작하도록
        # 전역 Peewee Proxy 모델 대신 연결 DB에 직접 UPSERT를 수행한다.
        sql = """
            INSERT INTO roots
                (root_id, root_path, real_path, label, updated_ts, created_ts)
            VALUES
                (?, ?, ?, ?, ?, ?)
            ON CONFLICT(root_id) DO UPDATE SET
                root_path = excluded.root_path,
                real_path = excluded.real_path,
                label = excluded.label,
                updated_ts = excluded.updated_ts
        """
        with self.db.atomic():
            self.execute(
                sql,
                (root_id, n_root_path, n_real_path, label, now, now),
            )

    def ensure_root(self, root_id: str, path: str):
        self.upsert_root(root_id, path, path)

    def upsert_files_tx(self, cur, rows: List[object]):
        """파일 정보를 트랜잭션 내에서 일괄 삽입합니다 (기본 방식)."""
        self._file_repo(cur).upsert_files_tx(cur, rows)

    def upsert_files_staging(self, cur, rows: List[object]):
        """파일 정보를 메모리 스테이징 테이블에 삽입합니다."""
        self._ensure_staging()
        col_names = ", ".join(FILE_COLUMNS)
        placeholders = ",".join(["?"] * len(FILE_COLUMNS))
        cur.executemany(
            f"INSERT OR REPLACE INTO staging_mem.files_temp({col_names}) VALUES ({placeholders})",
            rows)

    def upsert_files_turbo(self, rows: Iterable[object]):
        """
        대량의 파일 정보를 고속으로 처리합니다 (Turbo Mode).
        데이터를 튜플로 매핑하고 메모리 스테이징 테이블에 기록합니다.
        실제 DB 반영은 finalize_turbo_batch()에서 수행됩니다.
        """
        conn = self.db.connection()
        mapped_tuples = []
        now = int(time.time())
        for r in rows:
            if not r:
                continue
            try:
                if hasattr(r, "to_file_row"):
                    row_tuple = list(r.to_file_row())
                else:
                    row_tuple = list(r)
                while len(row_tuple) < len(FILE_COLUMNS):
                    row_tuple.append(None)
                data = dict(zip(FILE_COLUMNS, row_tuple, strict=False))
                path = data.get("path")
                if not path:
                    continue
                processed = {
                    "path": PathUtils.normalize(path),
                    "rel_path": data.get("rel_path") or re.split(r'[\\/]', str(path))[-1],
                    "root_id": data.get("root_id") or "root",
                    "repo": data.get("repo") or "",
                    "mtime": int(data.get("mtime") or now),
                    "size": int(data.get("size") or 0),
                    "content": data.get("content") or b"",
                    "hash": data.get("hash") or "",
                    "fts_content": data.get("fts_content") or "",
                    "last_seen_ts": int(data.get("last_seen_ts") or now),
                    "deleted_ts": int(data.get("deleted_ts") or 0),
                    "status": data.get("status") or "ok",
                    "error": data.get("error"),
                    "parse_status": data.get("parse_status") or "ok",
                    "parse_error": data.get("parse_error"),
                    "ast_status": data.get("ast_status") or "none",
                    "ast_reason": data.get("ast_reason") or "none",
                    "is_binary": int(data.get("is_binary") or 0),
                    "is_minified": int(data.get("is_minified") or 0),
                    "metadata_json": data.get("metadata_json") or "{}"
                }
                mapped_tuples.append(
                    tuple(processed[col] for col in FILE_COLUMNS))
            except Exception as e:
                self.logger.error("Failed to map turbo row: %s", e)
                continue

        if not mapped_tuples:
            return
        try:
            self.upsert_files_staging(conn, mapped_tuples)
            conn.commit()
        except Exception as e:
            self.logger.error(
                "upsert_files_turbo commit failed: %s",
                e,
                exc_info=True)
            raise

    def finalize_turbo_batch(self):
        """메모리 스테이징 테이블의 데이터를 메인 DB 파일로 일괄 이동하고 반영합니다."""
        conn = self.db.connection()
        try:
            res = conn.execute(
                "SELECT count(*) FROM staging_mem.files_temp").fetchone()
            count = int(next(iter(res), 0)) if res else 0
            if count == 0:
                return
            try:
                conn.execute("BEGIN IMMEDIATE TRANSACTION")
                cols = ", ".join(FILE_COLUMNS)
                conn.execute(
                    f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM staging_mem.files_temp")
                conn.execute("DELETE FROM staging_mem.files_temp")
                conn.execute("COMMIT")
                self.update_stats()
            except Exception as te:
                self.logger.error(
                    "Database merge failed: %s", te, exc_info=True)
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise te
        except Exception as e:
            self.logger.error("Critical error in finalize_turbo_batch: %s", e)
            raise

    def update_stats(self):
        """루트별 파일 및 심볼 통계를 갱신합니다."""
        try:
            conn = self.db.connection()
            conn.execute(
                "UPDATE roots SET file_count = (SELECT COUNT(1) FROM files WHERE files.root_id = roots.root_id AND deleted_ts = 0)")
            conn.execute(
                "UPDATE roots SET symbol_count = (SELECT COUNT(1) FROM symbols WHERE symbols.root_id = roots.root_id)")
            if not getattr(conn, "in_transaction", False):
                conn.commit()
        except Exception as e:
            self.logger.error("Failed to update statistics: %s", e)

    def get_repo_stats(
            self, root_ids: Optional[List[str]] = None) -> Dict[str, int]:
        """레포지토리별 파일 수 통계를 반환합니다."""
        try:
            sql = "SELECT label, file_count FROM roots"
            params: list[object] = []
            if root_ids:
                placeholders = ",".join(["?"] * len(root_ids))
                sql += f" WHERE root_id IN ({placeholders})"
                params.extend([str(rid) for rid in root_ids])
            rows = self.execute(sql, tuple(params) if params else None).fetchall() or []
            out: Dict[str, int] = {}
            for row in rows:
                label, count = normalize_repo_stat_row(row)
                out[label] = count
            return out
        except Exception:
            return self._file_repo().get_repo_stats(root_ids=root_ids)

    def execute(self, sql: str, params: Optional[Tuple[object, ...]] = None):
        """얇은 SQL 실행 헬퍼 (상태/진단 경로에서 사용)."""
        conn = self.db.connection()
        if params is None:
            return conn.execute(sql)
        return conn.execute(sql, params)

    def get_roots(self) -> List[Dict[str, object]]:
        """워크스페이스 루트 메타/통계를 대시보드 친화 포맷으로 반환합니다."""
        sql = """
            SELECT
                r.root_id AS root_id,
                r.root_path AS root_path,
                r.real_path AS real_path,
                r.label AS label,
                r.state AS state,
                COALESCE(r.created_ts, 0) AS created_ts,
                COALESCE(r.updated_ts, 0) AS updated_ts,
                COALESCE(r.last_scan_ts, 0) AS last_scan_ts,
                COALESCE(fc.file_count, 0) AS file_count,
                COALESCE(fc.last_indexed_ts, 0) AS last_indexed_ts,
                COALESCE(sc.symbol_count, 0) AS symbol_count
            FROM roots r
            LEFT JOIN (
                SELECT
                    root_id,
                    COUNT(1) AS file_count,
                    MAX(last_seen_ts) AS last_indexed_ts
                FROM files
                WHERE deleted_ts = 0
                GROUP BY root_id
            ) fc ON fc.root_id = r.root_id
            LEFT JOIN (
                SELECT root_id, COUNT(1) AS symbol_count
                FROM symbols
                GROUP BY root_id
            ) sc ON sc.root_id = r.root_id
            ORDER BY r.root_path
        """
        rows = self.execute(sql).fetchall() or []
        return [normalize_root_row(row) for row in rows]

    def read_file(self, path: str) -> Optional[str]:
        """특정 경로의 파일 내용을 DB에서 읽어 반환합니다 (압축 해제 포함)."""
        # Critical: let OperationalError (corruption) bubble up!
        normalized_path = PathUtils.normalize(path)
        db_path = self._resolve_db_path(normalized_path)
        candidates: list[str] = []
        for candidate in (normalized_path, db_path):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        if not candidates:
            return None
        placeholders = ",".join(["?"] * len(candidates))
        sql = f"""
            SELECT content
            FROM files
            WHERE path IN ({placeholders}) OR rel_path IN ({placeholders})
            LIMIT 1
        """
        row = self.execute(sql, tuple(candidates + candidates)).fetchone()
        if not row:
            return None
        content = row_content_value(row)
        try:
            return decode_file_content(content, db_path)
        except RuntimeError as de:
            self.logger.error("Decompression failed for %s: %s", db_path, de)
            raise

    def search_files(self, query: str, limit: int = 50) -> List[Dict]:
        """파일 경로 또는 내용을 기준으로 단순 검색을 수행합니다."""
        lq = f"%{query}%"
        # No try-except here! Let the engine errors be seen.
        rows = self.execute(
            """
            SELECT * FROM files
            WHERE deleted_ts = 0
              AND (path LIKE ? OR rel_path LIKE ? OR fts_content LIKE ?)
            LIMIT ?
            """,
            (lq, lq, lq, int(limit)),
        ).fetchall() or []
        out: List[Dict] = []
        for row in rows:
            normalized = normalize_search_row(row, FILE_COLUMNS)
            if normalized:
                out.append(normalized)
        return out

    def list_files(self,
                   limit: int = 50,
                   repo: Optional[str] = None,
                   root_ids: Optional[List[str]] = None) -> List[Dict]:
        """조건에 맞는 파일 목록을 반환합니다."""
        return self._file_repo().list_files(limit=limit, repo=repo, root_ids=root_ids)

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        """파일의 메타데이터(크기, 수정시간, 해시)를 반환합니다."""
        try:
            return self._file_repo().get_file_meta(self._resolve_db_path(path))
        except Exception as e:
            self.logger.debug("Failed to get file meta for %s: %s", path, e)
            return None

    def upsert_symbols_tx(self, cur, rows: List[object], root_id: str = "root"):
        """심볼 정보를 트랜잭션 내에서 일괄 삽입합니다."""
        if not rows:
            return
        if cur is None:
            cur = self.db.connection().cursor()
        self._symbol_repo(cur).upsert_symbols_tx(cur, rows)

    def upsert_relations_tx(self, cur, rows: List[object]):
        """관계 정보를 트랜잭션 내에서 일괄 삽입합니다."""
        if not rows:
            return
        if cur is None:
            cur = self.db.connection().cursor()
        self._symbol_repo(cur).upsert_relations_tx(cur, rows)

    def upsert_snippet_tx(self, cur, rows: List[object]):
        self._snippet_repo(cur).upsert_snippet_tx(cur, rows)

    def list_snippets_by_tag(
            self,
            tag: str,
            limit: int = 20) -> List[SnippetDTO]:
        return self._snippet_repo().list_snippets_by_tag(tag, limit=limit)

    def search_snippets(self, query: str, limit: int = 20) -> List[SnippetDTO]:
        return self._snippet_repo().search_snippets(query, limit=limit)

    def list_snippet_versions(self, snippet_id: int) -> List[Dict[str, object]]:
        return self._snippet_repo().list_snippet_versions(snippet_id)

    def update_snippet_location_tx(
        self,
        cur,
        snippet_id: int,
        start: int,
        end: int,
        content: str,
        content_hash: str,
        anchor_before: str,
        anchor_after: str,
        updated_ts: int,
    ) -> None:
        self._snippet_repo(cur).update_snippet_location_tx(
            cur,
            snippet_id,
            start,
            end,
            content,
            content_hash,
            anchor_before,
            anchor_after,
            updated_ts,
        )

    def upsert_context_tx(self, cur, rows: List[object]):
        self._context_repo(cur).upsert_context_tx(cur, rows)

    def search_contexts(self, query: str, limit: int = 20) -> List[ContextDTO]:
        return self._context_repo().search_contexts(query, limit=limit)

    def prune_stale_data(self, root_id: str, active_paths: List[str]):
        """더 이상 존재하지 않는 파일 데이터를 DB에서 정리(제거)합니다."""
        if active_paths:
            conn = self.db.connection()
            try:
                # Use a temp table to efficiently find stale paths
                conn.execute("CREATE TEMP TABLE IF NOT EXISTS _active_paths(path TEXT PRIMARY KEY)")
                conn.execute("DELETE FROM _active_paths")
                
                chunk_size = 1000
                for i in range(0, len(active_paths), chunk_size):
                    chunk = [(p,) for p in active_paths[i:i + chunk_size] if p]
                    if not chunk:
                        continue
                    conn.executemany("INSERT OR IGNORE INTO _active_paths(path) VALUES (?)", chunk)
                
                # Delete in smaller batches to avoid long locks and WAL explosion
                # Identify paths to delete first
                stale_paths = [
                    r[0] for r in conn.execute(
                        "SELECT path FROM files WHERE root_id = ? AND path NOT IN (SELECT path FROM _active_paths)",
                        (root_id,)
                    ).fetchall()
                ]
                
                if stale_paths:
                    for i in range(0, len(stale_paths), chunk_size):
                        batch = stale_paths[i:i + chunk_size]
                        placeholders = ",".join(["?"] * len(batch))
                        with self.db.atomic():
                            conn.execute(
                                f"DELETE FROM files WHERE path IN ({placeholders})",
                                tuple(batch),
                            )
                
                conn.execute("DELETE FROM _active_paths")
            except Exception as e:
                self.logger.error("Failed to prune stale data: %s", e)
                raise
        else:
            # Full cleanup for a root - also better in batches if possible, 
            # but usually for a single root it's manageable. 
            # Still, we use atomic to be safe.
            with self.db.atomic():
                conn = self.db.connection()
                conn.execute("DELETE FROM files WHERE root_id = ?", (str(root_id),))

    def delete_path_tx(self, cur, path: str):
        self._file_repo(cur).delete_path_tx(cur, path)

    def update_last_seen_tx(self, cur, paths: List[str], ts: int):
        self._file_repo(cur).update_last_seen_tx(cur, paths, ts)

    def search_symbols(
            self,
            query: str,
            limit: int = 20,
            **kwargs) -> List[Dict]:
        """심볼 이름으로 검색을 수행합니다."""
        return [
            s.model_dump() for s in self._symbol_repo().search_symbols(
                query, limit=limit, **kwargs)]

    def search(self, opts: SearchOptions):
        """Canonical search interface (engine delegated or repository fallback)."""
        if self.engine:
            try:
                search_fn = self.engine.search
            except AttributeError:
                search_fn = None
            if callable(search_fn):
                return search_fn(opts)
        repo = self._search_repo()
        return repo.search(opts)

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[List[str]] = None) -> List[Dict[str, object]]:
        if self.engine and hasattr(self.engine, "repo_candidates"):
            return self.engine.repo_candidates(
                q, limit=limit, root_ids=root_ids)
        return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)

    def _search_sqlite(self, opts: SearchOptions):
        """SQLite-only fallback search path (engine bypass)."""
        repo = self._search_repo()
        return repo.search(opts)

    def repo_candidates_sqlite(self, q: str, limit: int = 3,
                               root_ids: Optional[List[str]] = None) -> List[Dict[str, object]]:
        """SQLite-only repo candidate path (engine bypass)."""
        return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)

    def apply_root_filter(
            self, sql: str, root_id: Optional[str]) -> Tuple[str, List[object]]:
        return _apply_root_filter_impl(sql, root_id)

    def count_failed_tasks(self) -> Tuple[int, int]:
        return self.tasks.count_failed_tasks()

    def register_writer_thread(self, thread_id: Optional[int]) -> None:
        self._writer_thread_id = int(
            thread_id) if thread_id is not None else None

    def get_symbol_fan_in_stats(
            self, symbol_names: List[str]) -> Dict[str, int]:
        """심볼의 참조 횟수(Fan-in) 통계를 반환합니다."""
        return self._symbol_repo().get_symbol_fan_in_stats(symbol_names)

    def has_legacy_paths(self) -> bool: return False
    def set_engine(self, engine): self.engine = engine

    def swap_db_file(self, new_path: str):
        """
        워커 프로세스에서 생성된 스냅샷 DB의 내용을 메인 DB로 병합합니다.
        ATTACH DATABASE를 사용하여 테이블 간 데이터 복사를 수행합니다.
        """
        if not new_path or not os.path.exists(new_path):
            return
        conn = self.db.connection()
        attached = False
        try:
            conn.execute("ATTACH DATABASE ? AS snapshot", (new_path,))
            attached = True
            conn.execute("BEGIN IMMEDIATE TRANSACTION")
            try:
                for tbl in [
                    "roots",
                    "files",
                    "symbols",
                    "symbol_relations",
                    "snippets",
                    "failed_tasks",
                        "embeddings"]:
                    if tbl == "files":
                        cols = ", ".join(FILE_COLUMNS)
                        conn.execute(
                            f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM snapshot.files")
                    else:
                        conn.execute(
                            f"INSERT OR REPLACE INTO main.{tbl} SELECT * FROM snapshot.{tbl}")
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            self.update_stats()
        except Exception as e:
            self.logger.error("Failed to swap DB file: %s", e, exc_info=True)
            raise
        finally:
            if attached:
                try:
                    conn.execute("DETACH DATABASE snapshot")
                except Exception as de:
                    self.logger.debug("Failed to detach snapshot: %s", de)

    def get_connection(self): return self.db.connection()

    def get_read_connection(self):
        conn = self.db.connection()
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def _write(self): return self.db.connection()
    @property
    def _read(self): return self.get_read_connection()
    def _get_conn(self): return self.db.connection()

    # 하위 리포지토리 접근 프로퍼티 (지연 로딩)
    @property
    def files(self): return self._file_repo()
    @property
    def symbols(self): return self._symbol_repo()
    @property
    def snippets(self): return self._snippet_repo()
    @property
    def search_repo(self): return self._search_repo()

    @property
    def tasks(self):
        from sari.core.repository.failed_task_repository import FailedTaskRepository
        return FailedTaskRepository(self.db.connection())

    @property
    def contexts(self):
        return self._context_repo()

    def _resolve_db_path(self, path: str) -> str:
        """절대 경로를 DB 내부의 상대 경로(root_id/rel_path)로 변환합니다."""
        if os.path.isabs(path):
            from sari.core.workspace import WorkspaceManager
            # Normalize path casing for consistent root lookup (especially on Windows)
            norm_path = PathUtils.normalize(path)
            root = WorkspaceManager.find_root_for_path(norm_path)
            if root:
                rid = WorkspaceManager.root_id(root)
                rel = PathUtils.to_relative(norm_path, root)
                return f"{rid}/{rel}"
        return path

    def _file_repo(self, cur=None):
        from sari.core.repository.file_repository import FileRepository
        return FileRepository(self._get_real_conn(cur))

    def _symbol_repo(self, cur=None):
        from sari.core.repository.symbol_repository import SymbolRepository
        return SymbolRepository(self._get_real_conn(cur))

    def _search_repo(self, cur=None):
        from sari.core.repository.search_repository import SearchRepository
        return SearchRepository(self._get_real_conn(cur))

    def _snippet_repo(self, cur=None):
        from sari.core.repository.extra_repository import SnippetRepository
        return SnippetRepository(self._get_real_conn(cur))

    def _context_repo(self, cur=None):
        from sari.core.repository.extra_repository import ContextRepository
        return ContextRepository(self._get_real_conn(cur))

    def _get_real_conn(self, cur):
        if cur is not None:
            if hasattr(cur, "connection"):
                return cur.connection
            if hasattr(cur, "execute"):
                return cur
        return self.db.connection()

    def close(self): self.db.close()
    def close_all(self): self.close()
