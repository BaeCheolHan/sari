import sqlite3
import time
import logging
import os
import zlib
import threading
from typing import List, Dict, Any, Optional, Tuple, Iterable
from peewee import SqliteDatabase, OperationalError, IntegrityError
from .models import (
    db_proxy, Root, File, Symbol, FileDTO, IndexingResult, 
    SnippetDTO, ContextDTO, FILE_COLUMNS, _to_dict
)
from .schema import init_schema
from ..utils.path import PathUtils

logger = logging.getLogger("sari.db")

class LocalSearchDB:
    def __init__(self, db_path: str, logger_obj: Optional[logging.Logger] = None, **kwargs):
        self.db_path = db_path
        self.logger = logger_obj or logger
        self.engine = None
        self._lock = threading.Lock()
        try:
            self.db = SqliteDatabase(db_path, pragmas={
                'journal_mode': kwargs.get('journal_mode', 'wal'),
                'cache_size': -10000,
                'foreign_keys': 1,
                'ignore_check_constraints': 0,
                'synchronous': 0,
                'busy_timeout': 15000
            })
            db_proxy.initialize(self.db)
            self.db.connect()
            init_schema(self.db.connection())
            self._init_mem_staging()
        except Exception as e:
            self.logger.error("Failed to initialize database: %s", e, exc_info=True)
            raise

    def _init_mem_staging(self, force=True):
        try:
            conn = self.db.connection()
            dbs = [row[1] for row in conn.execute("PRAGMA database_list").fetchall()]
            if "staging_mem" not in dbs:
                conn.execute("ATTACH DATABASE ':memory:' AS staging_mem")
            if force:
                conn.execute("DROP TABLE IF EXISTS staging_mem.files_temp")
            conn.execute("CREATE TABLE IF NOT EXISTS staging_mem.files_temp AS SELECT * FROM main.files WHERE 0")
        except Exception as e:
            self.logger.error("Failed to init mem staging: %s", e, exc_info=True)
            raise

    def _ensure_staging(self): 
        try: self._init_mem_staging(force=False)
        except Exception: self._init_mem_staging(force=True)

    def create_staging_table(self, cur=None):
        self._init_mem_staging(force=True)

    def upsert_root(self, root_id: str, root_path: str, real_path: str, **kwargs):
        with self.db.atomic():
            Root.insert(
                root_id=root_id, root_path=PathUtils.normalize(root_path), real_path=PathUtils.normalize(real_path),
                label=kwargs.get("label", PathUtils.normalize(root_path).split("/")[-1]),
                updated_ts=int(time.time()), created_ts=int(time.time())
            ).on_conflict_replace().execute()

    def ensure_root(self, root_id: str, path: str):
        self.upsert_root(root_id, path, path)

    def upsert_files_tx(self, cur, rows: List[tuple]):
        self._file_repo(cur).upsert_files_tx(cur, rows)

    def upsert_files_staging(self, cur, rows: List[tuple]):
        self._ensure_staging()
        col_names = ", ".join(FILE_COLUMNS)
        placeholders = ",".join(["?"] * len(FILE_COLUMNS))
        cur.executemany(f"INSERT OR REPLACE INTO staging_mem.files_temp({col_names}) VALUES ({placeholders})", rows)

    def upsert_files_turbo(self, rows: Iterable[Any]):
        conn = self.db.connection()
        mapped_tuples = []
        now = int(time.time())
        for r in rows:
            if not r: continue
            try:
                if hasattr(r, "to_file_row"): row_tuple = list(r.to_file_row())
                else: row_tuple = list(r)
                while len(row_tuple) < len(FILE_COLUMNS): row_tuple.append(None)
                data = dict(zip(FILE_COLUMNS, row_tuple))
                path = data.get("path")
                if not path: continue
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
                mapped_tuples.append(tuple(processed[col] for col in FILE_COLUMNS))
            except Exception as e:
                self.logger.error("Failed to map turbo row: %s", e); continue

        if not mapped_tuples: return
        try: 
            self.upsert_files_staging(conn, mapped_tuples)
            conn.commit()
        except Exception as e:
            self.logger.error("upsert_files_turbo commit failed: %s", e, exc_info=True); raise

    def finalize_turbo_batch(self):
        conn = self.db.connection()
        try:
            res = conn.execute("SELECT count(*) FROM staging_mem.files_temp").fetchone()
            count = res[0] if res else 0
            if count == 0: return
            try:
                conn.execute("BEGIN IMMEDIATE TRANSACTION")
                cols = ", ".join(FILE_COLUMNS)
                conn.execute(f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM staging_mem.files_temp")
                conn.execute("DELETE FROM staging_mem.files_temp")
                conn.execute("COMMIT")
                self.update_stats()
            except Exception as te:
                self.logger.error("Database merge failed: %s", te, exc_info=True)
                try: conn.execute("ROLLBACK")
                except Exception: pass
                raise te
        except Exception as e:
            self.logger.error("Critical error in finalize_turbo_batch: %s", e); raise

    def update_stats(self):
        try:
            conn = self.db.connection()
            conn.execute("UPDATE roots SET file_count = (SELECT COUNT(1) FROM files WHERE files.root_id = roots.root_id AND deleted_ts = 0)")
            conn.execute("UPDATE roots SET symbol_count = (SELECT COUNT(1) FROM symbols WHERE symbols.root_id = roots.root_id)")
            conn.commit()
        except Exception as e: self.logger.error("Failed to update statistics: %s", e)

    def get_repo_stats(self, root_ids: Optional[List[str]] = None) -> Dict[str, int]:
        try:
            query = Root.select(Root.label, Root.file_count)
            if root_ids: query = query.where(Root.root_id << root_ids)
            return {r.label: r.file_count for r in query}
        except Exception: return self._file_repo().get_repo_stats(root_ids=root_ids)

    def read_file(self, path: str) -> Optional[str]:
        # Critical: let OperationalError (corruption) bubble up!
        db_path = self._resolve_db_path(path)
        row = File.select(File.content).where(File.path == db_path).first()
        if not row: return None
        content = row.content
        if isinstance(content, bytes) and content.startswith(b"ZLIB\0"):
            try: content = zlib.decompress(content[5:])
            except Exception as de:
                self.logger.error("Decompression failed for %s: %s", db_path, de)
                return None
        if isinstance(content, bytes): return content.decode("utf-8", errors="ignore")
        return str(content)

    def search_files(self, query: str, limit: int = 50) -> List[Dict]:
        lq = f"%{query}%"
        # No try-except here! Let the engine errors be seen.
        return list(File.select().where((File.path ** lq) | (File.rel_path ** lq) | (File.fts_content ** lq)).where(File.deleted_ts == 0).limit(limit).dicts())

    def list_files(self, limit: int = 50, repo: Optional[str] = None, root_ids: Optional[List[str]] = None) -> List[Dict]:
        return self._file_repo().list_files(limit=limit, repo=repo, root_ids=root_ids)

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        try: return self._file_repo().get_file_meta(self._resolve_db_path(path))
        except Exception: return None

    def upsert_symbols_tx(self, cur, rows: List[tuple], root_id: str = "root"):
        if not rows: return
        if cur is None: cur = self.db.connection().cursor()
        self._symbol_repo(cur).upsert_symbols_tx(cur, rows)

    def upsert_snippet_tx(self, cur, rows: List[tuple]):
        self._snippet_repo(cur).upsert_snippet_tx(cur, rows)

    def list_snippets_by_tag(self, tag: str) -> List[SnippetDTO]:
        return self._snippet_repo().list_snippets_by_tag(tag)

    def upsert_context_tx(self, cur, rows: List[tuple]):
        self._context_repo(cur).upsert_context_tx(cur, rows)

    def search_contexts(self, query: str, limit: int = 20) -> List[ContextDTO]:
        return self._context_repo().search_contexts(query, limit=limit)

    def prune_stale_data(self, root_id: str, active_paths: List[str]):
        with self.db.atomic():
            if active_paths: File.delete().where((File.root == root_id) & (File.path.not_in(active_paths))).execute()
            else: File.delete().where(File.root == root_id).execute()

    def delete_path_tx(self, cur, path: str):
        self._file_repo(cur).delete_path_tx(cur, path)

    def update_last_seen_tx(self, cur, paths: List[str], ts: int):
        self._file_repo(cur).update_last_seen_tx(cur, paths, ts)

    def search_symbols(self, query: str, limit: int = 20, **kwargs) -> List[Dict]:
        return [s.model_dump() for s in self._symbol_repo().search_symbols(query, limit=limit, **kwargs)]

    def search_v2(self, opts: Any):
        if self.engine and hasattr(self.engine, "search_v2"): return self.engine.search_v2(opts)
        return self._search_repo().search_v2(opts)

    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if self.engine and hasattr(self.engine, "repo_candidates"): return self.engine.repo_candidates(q, limit=limit, root_ids=root_ids)
        return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)

    def get_symbol_fan_in_stats(self, symbol_names: List[str]) -> Dict[str, int]:
        return self._symbol_repo().get_symbol_fan_in_stats(symbol_names)

    def has_legacy_paths(self) -> bool: return False
    def set_engine(self, engine): self.engine = engine
    
    def swap_db_file(self, new_path: str):
        if not new_path or not os.path.exists(new_path): return
        conn = self.db.connection()
        try:
            conn.execute("ATTACH DATABASE ? AS snapshot", (new_path,))
            with self.db.atomic():
                for tbl in ["roots", "files", "symbols", "symbol_relations", "snippets", "failed_tasks", "embeddings"]:
                    try:
                        if tbl == "files":
                            cols = ", ".join(FILE_COLUMNS)
                            conn.execute(f"INSERT OR REPLACE INTO main.files({cols}) SELECT {cols} FROM snapshot.files")
                        else: conn.execute(f"INSERT OR REPLACE INTO main.{tbl} SELECT * FROM snapshot.{tbl}")
                    except Exception: pass
            self.update_stats()
        except Exception as e: self.logger.error("Failed to swap DB file: %s", e, exc_info=True); raise
        finally:
            try: conn.execute("DETACH DATABASE snapshot")
            except Exception: pass

    def get_connection(self): return self.db.connection()
    def get_read_connection(self): 
        conn = self.db.connection(); conn.row_factory = sqlite3.Row; return conn

    @property
    def _write(self): return self.db.connection()
    @property
    def _read(self): return self.get_read_connection()
    def _get_conn(self): return self.db.connection()

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

    def _resolve_db_path(self, path: str) -> str:
        if os.path.isabs(path):
            from sari.core.workspace import WorkspaceManager
            root = WorkspaceManager.find_root_for_path(path)
            if root:
                rid = WorkspaceManager.root_id(root)
                rel = PathUtils.to_relative(path, root)
                return f"{rid}/{rel}"
        return path

    def _file_repo(self, cur=None) -> "FileRepository":
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
            if hasattr(cur, "connection"): return cur.connection
            if hasattr(cur, "execute"): return cur
        return self.db.connection()

    def close(self): self.db.close()
    def close_all(self): self.close()