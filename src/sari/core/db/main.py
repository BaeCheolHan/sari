import sqlite3
import time
import logging
import os
import zlib
import threading
from typing import List, Dict, Any, Optional, Tuple, Iterable
from peewee import SqliteDatabase
from .models import db_proxy, Root, File
from ..models import SnippetDTO, ContextDTO, FILE_COLUMNS
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
                 **kwargs):
        """
        Args:
            db_path: SQLite 데이터베이스 파일 경로
            logger_obj: 로거 객체 (기본값: sari.db)
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
        try:
            conn = self.db.connection()
            dbs = [row[1]
                   for row in conn.execute("PRAGMA database_list").fetchall()]
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
        with self.db.atomic():
            Root.insert(root_id=root_id,
                        root_path=PathUtils.normalize(root_path),
                        real_path=PathUtils.normalize(real_path),
                        label=kwargs.get("label",
                                         PathUtils.normalize(root_path).split("/")[-1]),
                        updated_ts=int(time.time()),
                        created_ts=int(time.time())).on_conflict_replace().execute()

    def ensure_root(self, root_id: str, path: str):
        self.upsert_root(root_id, path, path)

    def upsert_files_tx(self, cur, rows: List[tuple]):
        """파일 정보를 트랜잭션 내에서 일괄 삽입합니다 (기본 방식)."""
        self._file_repo(cur).upsert_files_tx(cur, rows)

    def upsert_files_staging(self, cur, rows: List[tuple]):
        """파일 정보를 메모리 스테이징 테이블에 삽입합니다."""
        self._ensure_staging()
        col_names = ", ".join(FILE_COLUMNS)
        placeholders = ",".join(["?"] * len(FILE_COLUMNS))
        cur.executemany(
            f"INSERT OR REPLACE INTO staging_mem.files_temp({col_names}) VALUES ({placeholders})",
            rows)

    def upsert_files_turbo(self, rows: Iterable[Any]):
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
                data = dict(zip(FILE_COLUMNS, row_tuple))
                path = data.get("path")
                if not path:
                    continue
                processed = {
                    "path": PathUtils.normalize(path),
                    "rel_path": data.get("rel_path") or os.path.basename(str(path)),
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
            count = res[0] if res else 0
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
            conn.commit()
        except Exception as e:
            self.logger.error("Failed to update statistics: %s", e)

    def get_repo_stats(
            self, root_ids: Optional[List[str]] = None) -> Dict[str, int]:
        """레포지토리별 파일 수 통계를 반환합니다."""
        try:
            query = Root.select(Root.label, Root.file_count)
            if root_ids:
                query = query.where(Root.root_id << root_ids)
            return {r.label: r.file_count for r in query}
        except Exception:
            return self._file_repo().get_repo_stats(root_ids=root_ids)

    def read_file(self, path: str) -> Optional[str]:
        """특정 경로의 파일 내용을 DB에서 읽어 반환합니다 (압축 해제 포함)."""
        # Critical: let OperationalError (corruption) bubble up!
        db_path = self._resolve_db_path(path)
        row = File.select(File.content).where(File.path == db_path).first()
        if not row:
            return None
        content = row.content
        if isinstance(content, bytes) and content.startswith(b"ZLIB\0"):
            try:
                content = zlib.decompress(content[5:])
            except Exception as de:
                self.logger.error(
                    "Decompression failed for %s: %s", db_path, de)
                return None
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="ignore")
        return str(content)

    def search_files(self, query: str, limit: int = 50) -> List[Dict]:
        """파일 경로 또는 내용을 기준으로 단순 검색을 수행합니다."""
        lq = f"%{query}%"
        # No try-except here! Let the engine errors be seen.
        return list(File.select().where((File.path ** lq) | (File.rel_path ** lq) |
                    (File.fts_content ** lq)).where(File.deleted_ts == 0).limit(limit).dicts())

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

    def upsert_symbols_tx(self, cur, rows: List[tuple], root_id: str = "root"):
        """심볼 정보를 트랜잭션 내에서 일괄 삽입합니다."""
        if not rows:
            return
        if cur is None:
            cur = self.db.connection().cursor()
        self._symbol_repo(cur).upsert_symbols_tx(cur, rows)

    def upsert_relations_tx(self, cur, rows: List[tuple]):
        """관계 정보를 트랜잭션 내에서 일괄 삽입합니다."""
        if not rows:
            return
        if cur is None:
            cur = self.db.connection().cursor()
        self._symbol_repo(cur).upsert_relations_tx(cur, rows)

    def upsert_snippet_tx(self, cur, rows: List[tuple]):
        self._snippet_repo(cur).upsert_snippet_tx(cur, rows)

    def list_snippets_by_tag(
            self,
            tag: str,
            limit: int = 20) -> List[SnippetDTO]:
        return self._snippet_repo().list_snippets_by_tag(tag, limit=limit)

    def search_snippets(self, query: str, limit: int = 20) -> List[SnippetDTO]:
        return self._snippet_repo().search_snippets(query, limit=limit)

    def list_snippet_versions(self, snippet_id: int) -> List[Dict[str, Any]]:
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

    def upsert_context_tx(self, cur, rows: List[tuple]):
        self._context_repo(cur).upsert_context_tx(cur, rows)

    def search_contexts(self, query: str, limit: int = 20) -> List[ContextDTO]:
        return self._context_repo().search_contexts(query, limit=limit)

    def prune_stale_data(self, root_id: str, active_paths: List[str]):
        """더 이상 존재하지 않는 파일 데이터를 DB에서 정리(제거)합니다."""
        with self.db.atomic():
            if active_paths:
                File.delete().where(
                    (File.root == root_id) & (
                        File.path.not_in(active_paths))).execute()
            else:
                File.delete().where(File.root == root_id).execute()

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

    def search_v2(self, opts: Any):
        """V2 검색 인터페이스 (엔진 위임 또는 저장소 직접 검색)."""
        if self.engine and hasattr(self.engine, "search_v2"):
            return self.engine.search_v2(opts)
        return self._search_repo().search_v2(opts)

    def repo_candidates(self, q: str, limit: int = 3,
                        root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if self.engine and hasattr(self.engine, "repo_candidates"):
            return self.engine.repo_candidates(
                q, limit=limit, root_ids=root_ids)
        return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)

    def search_sqlite_v2(self, opts: Any):
        """SQLite-only fallback search path (engine bypass)."""
        return self._search_repo().search_v2(opts)

    def repo_candidates_sqlite(self, q: str, limit: int = 3,
                               root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """SQLite-only repo candidate path (engine bypass)."""
        return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)

    def apply_root_filter(
            self, sql: str, root_id: Optional[str]) -> Tuple[str, List[Any]]:
        sql = str(sql or "").strip()
        if not sql:
            return sql, []
        has_where = " where " in sql.lower()
        params: List[Any] = []
        if root_id:
            if has_where:
                sql += " AND root_id = ?"
            else:
                sql += " WHERE root_id = ?"
            params.append(str(root_id))
        elif not has_where:
            sql += " WHERE 1=1"
        return sql, params

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
        try:
            conn.execute("ATTACH DATABASE ? AS snapshot", (new_path,))
            with self.db.atomic():
                for tbl in [
                    "roots",
                    "files",
                    "symbols",
                    "symbol_relations",
                    "snippets",
                    "failed_tasks",
                        "embeddings"]:
                    try:
                        if tbl == "files":
                            cols = ", ".join(FILE_COLUMNS)
                            conn.execute(
                                f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM snapshot.files")
                        else:
                            conn.execute(
                                f"INSERT OR REPLACE INTO main.{tbl} SELECT * FROM snapshot.{tbl}")
                    except Exception as te:
                        self.logger.error(
                            "Failed to copy table %s from snapshot: %s", tbl, te)
            self.update_stats()
        except Exception as e:
            self.logger.error("Failed to swap DB file: %s", e, exc_info=True)
            raise
        finally:
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
            root = WorkspaceManager.find_root_for_path(path)
            if root:
                rid = WorkspaceManager.root_id(root)
                rel = PathUtils.to_relative(path, root)
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
