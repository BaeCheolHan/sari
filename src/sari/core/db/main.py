import sqlite3
import logging
import time
import zlib
import threading
import os
import json
from typing import List, Dict, Any, Optional, Iterable, Tuple

try:
    from peewee import SqliteDatabase, fn, Proxy
    from .models import db_proxy, Root, File, Symbol, Snippet, Context
    HAS_PEEWEE = True
except ImportError:
    HAS_PEEWEE = False
    db_proxy = None

from .schema import init_schema
from sari.core.repository.extra_repository import SnippetRepository, ContextRepository
from sari.core.repository.symbol_repository import SymbolRepository
from sari.core.repository.search_repository import SearchRepository
from sari.core.repository.failed_task_repository import FailedTaskRepository

class LocalSearchDB:
    def __init__(self, db_path: str, logger=None, journal_mode: str = "wal"):
        self.db_path = db_path
        self.logger = logger or logging.getLogger("sari.db")
        self.settings = None
        self.engine = None
        self._swap_in_progress = threading.Event()
        if not HAS_PEEWEE: return
        dir_path = os.path.dirname(self.db_path)
        if self.db_path not in (":memory:", "") and dir_path:
            os.makedirs(dir_path, exist_ok=True)
        self.db = SqliteDatabase(self.db_path, pragmas={
            'journal_mode': journal_mode, 'synchronous': 'normal',
            'busy_timeout': 60000, 'foreign_keys': 1
        }, check_same_thread=False)
        self.db.connect(reuse_if_open=True)
        db_proxy.initialize(self.db)
        with self.db.atomic(): init_schema(self.db.connection())
        self._init_mem_staging()
        self._lock = threading.Lock()

    def _wait_for_swap(self, timeout: float = 2.0) -> None:
        if not self._swap_in_progress.is_set():
            return
        start = time.time()
        while self._swap_in_progress.is_set():
            if time.time() - start >= timeout:
                if self.logger:
                    self.logger.warning("DB swap wait timeout; proceeding with risk of stale data.")
                break
            time.sleep(0.01)

    def set_engine(self, engine): self.engine = engine
    def set_settings(self, settings): self.settings = settings

    @property
    def fts_enabled(self) -> bool:
        """Check if FTS5 is supported by the SQLite library."""
        try:
            conn = self.db.connection()
            res = conn.execute("PRAGMA compile_options").fetchall()
            options = [r[0] for r in res]
            return "ENABLE_FTS5" in options
        except Exception:
            return False

    def create_staging_table(self, cur=None): self._ensure_staging()

    @property
    def snippets(self) -> SnippetRepository:
        """Domain repository for code snippets."""
        return SnippetRepository(self._read)

    @property
    def contexts(self) -> ContextRepository:
        """Domain repository for knowledge contexts."""
        return ContextRepository(self._read)

    @property
    def symbols(self) -> SymbolRepository:
        """Domain repository for symbol analysis."""
        return SymbolRepository(self._read)

    @property
    def search_repo(self) -> SearchRepository:
        """Domain repository for search operations."""
        return SearchRepository(self._read)

    @property
    def tasks(self) -> FailedTaskRepository:
        """Domain repository for task management."""
        return FailedTaskRepository(self._read)

    def _snippet_repo(self) -> "SnippetRepository": return self.snippets
    def _context_repo(self) -> "ContextRepository": return self.contexts
    def _symbol_repo(self) -> "SymbolRepository": return self.symbols
    def _search_repo(self) -> "SearchRepository": return self.search_repo
    def _failed_task_repo(self) -> "FailedTaskRepository": return self.tasks

    def register_writer_thread(self, thread_id: int) -> None:
        """Allow external callers to register the current writer thread.

        Peewee's threadlocal handling can be sensitive in multi-threaded callers.
        We only track the id for compatibility with MCP tools.
        """
        self._writer_thread_id = thread_id

    def get_read_connection(self) -> sqlite3.Connection:
        return self._read

    def get_symbol_fan_in_stats(self, symbol_names: List[str]) -> Dict[str, int]:
        """Proxy to SymbolRepository for entropy analysis."""
        return self.symbols.get_symbol_fan_in_stats(symbol_names)

    def count_failed_tasks(self) -> Tuple[int, int]:
        """Return total failed tasks and those with high retry count."""
        try:
            return self._failed_task_repo().count_failed_tasks()
        except Exception:
            return 0, 0

    def _init_mem_staging(self):
        try:
            conn = self.db.connection()
            dbs = [row[1] for row in conn.execute("PRAGMA database_list").fetchall()]
            if "staging_mem" not in dbs:
                conn.execute("ATTACH DATABASE ':memory:' AS staging_mem")
            conn.execute("CREATE TABLE IF NOT EXISTS staging_mem.files_temp AS SELECT * FROM main.files WHERE 0")
        except Exception: pass

    def _ensure_staging(self): self._init_mem_staging()

    def upsert_root(self, root_id: str, root_path: str, real_path: str, **kwargs):
        with self.db.atomic():
            Root.insert(
                root_id=root_id, root_path=root_path, real_path=real_path,
                label=kwargs.get("label", root_path.split("/")[-1]),
                updated_ts=int(time.time()), created_ts=int(time.time())
            ).on_conflict_replace().execute()

    def ensure_root(self, root_id: str, path: str):
        with self.db.atomic():
            Root.insert(
                root_id=root_id,
                root_path=path,
                real_path=path,
                label=path.split("/")[-1]
            ).on_conflict_ignore().execute()        # DB에 저장
        self.db.commit()

    def list_snippets_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """Return snippet rows matching a tag (most recent first)."""
        try:
            return self._snippet_repo().list_snippets_by_tag(tag)
        except Exception:
            return []

    def get_context_by_topic(self, topic: str) -> Optional[Dict[str, Any]]:
        """Return a single context row for a topic."""
        try:
            return self._context_repo().get_context_by_topic(topic)
        except Exception:
            return None

    def search_contexts(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not query:
            return []
        try:
            return self._context_repo().search_contexts(query, limit=limit)
        except Exception:
            return []

    def upsert_context_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        return self._context_repo().upsert_context_tx(cur, rows)

    def upsert_snippet_tx(self, cur: sqlite3.Cursor, rows: Iterable[tuple]) -> int:
        return self._snippet_repo().upsert_snippet_tx(cur, rows)

    def update_snippet_location_tx(self, cur: sqlite3.Cursor, snippet_id: int, start: int, end: int, content: str,
                                   content_hash: str, anchor_before: str, anchor_after: str, updated_ts: int) -> None:
        self._snippet_repo().update_snippet_location_tx(
            cur,
            snippet_id=snippet_id,
            start=start,
            end=end,
            content=content,
            content_hash=content_hash,
            anchor_before=anchor_before,
            anchor_after=anchor_after,
            updated_ts=updated_ts,
        )

    def list_snippet_versions(self, snippet_id: int) -> List[Dict[str, Any]]:
        try:
            return self._snippet_repo().list_snippet_versions(snippet_id)
        except Exception:
            return []

    def search_snippets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not query:
            return []
        try:
            return self._snippet_repo().search_snippets(query, limit=limit)
        except Exception:
            return []

    def upsert_files_turbo(self, rows: Iterable[Any]):
        """Efficiently staging files into memory database. Now supports IndexingResult objects and raw tuples."""
        self._ensure_staging()
        conn = self.db.connection()
        mapped = []
        for r in rows:
            if not r: continue
            
            # Convert IndexingResult or similar objects to tuples
            if hasattr(r, "to_file_row"):
                raw = r.to_file_row()
            elif isinstance(r, tuple):
                raw = r
            else:
                continue

            base = [None] * 20
            for i in range(min(len(raw), 20)): base[i] = raw[i]
            
            # Fill NOT NULL defaults for resilience
            if base[0] is None: continue # Primary key must exist
            base[1] = base[1] or os.path.basename(str(base[0])) # rel_path
            base[2] = base[2] or "root" # root_id
            base[3] = base[3] or ""     # repo
            base[4] = base[4] or 0      # mtime
            base[5] = base[5] or 0      # size
            
            if base[6] is None: base[6] = b""
            elif isinstance(base[6], str): base[6] = base[6].encode("utf-8", errors="ignore")
            
            base[9] = base[9] or 0 # last_seen_ts
            base[10] = base[10] or 0 # deleted_ts
            mapped.append(tuple(base))
            
        if not mapped: return
        try: 
            placeholders = ",".join(["?"] * 20)
            conn.executemany(f"INSERT OR REPLACE INTO staging_mem.files_temp VALUES ({placeholders})", mapped)
            if conn.in_transaction: conn.commit()
        except Exception as e:
            if self.logger: self.logger.error("upsert_files_turbo failed: %s", e, exc_info=True)
            raise

    def upsert_files_tx(self, cur, rows: List[tuple]):
        """Direct write to main table for testing or small batches."""
        placeholders = ",".join(["?"] * 20)
        mapped = []
        for r in rows:
            base = [None] * 20
            for i in range(min(len(r), 20)): base[i] = r[i]
            if isinstance(base[6], str): base[6] = base[6].encode("utf-8", errors="ignore")
            mapped.append(tuple(base))
        sql = f"INSERT OR REPLACE INTO files VALUES ({placeholders})"
        if cur: cur.executemany(sql, mapped)
        else:
            with self.db.atomic(): self.db.execute_sql(sql, mapped)

    def finalize_turbo_batch(self):
        """Atomic merge staged memory data into the main persistent database."""
        conn = self.db.connection()
        try:
            if conn.in_transaction: conn.commit()
            self._ensure_staging()
            res = conn.execute("SELECT count(*) FROM staging_mem.files_temp").fetchone()
            count = res[0] if res else 0
            if count == 0: return
            try:
                # Use IMMEDIATE to lock the database early and prevent busy-waits
                conn.execute("BEGIN IMMEDIATE TRANSACTION")
                conn.execute("INSERT OR REPLACE INTO main.files SELECT * FROM staging_mem.files_temp")
                conn.execute("DELETE FROM staging_mem.files_temp")
                conn.execute("COMMIT")
                if self.logger: self.logger.debug("Finalized turbo batch: %d files merged.", count)
            except Exception as te:
                if self.logger: self.logger.error("Database merge failed: %s", te)
                try: conn.execute("ROLLBACK")
                except: pass
                raise te
            finally:
                try: conn.execute("PRAGMA foreign_keys = ON")
                except: pass
        except Exception as e:
            if self.logger: self.logger.error("Critical error in finalize_turbo_batch: %s", e)
            raise
    def upsert_symbols_tx(self, cur, rows: List[tuple], root_id: str = "root"):
        if not rows:
            return
        try:
            if cur is None:
                cur = self._write.cursor()
            self._symbol_repo().upsert_symbols_tx(cur, rows)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to upsert symbols: {e}")
            raise

    def search_files(self, query: str, limit: int = 50) -> List[Dict]:
        lq = f"%{query}%"
        return list(File.select().where((File.path ** lq) | (File.rel_path ** lq) | (File.fts_content ** lq)).where(File.deleted_ts == 0).limit(limit).dicts())

    def search_symbols(self, query: str, limit: int = 20, **kwargs) -> List[Dict]:
        lq = f"%{query}%"
        q = (Symbol.select(Symbol, File.repo).join(File, on=(Symbol.path == File.path)).where((Symbol.name ** lq) | (Symbol.qualname ** lq)))
        if kwargs.get("kinds"): q = q.where(Symbol.kind << kwargs.get("kinds"))
        elif kwargs.get("kind"): q = q.where(Symbol.kind == kwargs.get("kind"))
        if kwargs.get("repo"): q = q.where(File.repo == kwargs.get("repo"))
        if kwargs.get("root_ids"): q = q.where(Symbol.root_id << kwargs.get("root_ids"))
        return list(q.limit(limit).dicts())

    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not q:
            return []
        try:
            if self.engine and hasattr(self.engine, "repo_candidates"):
                return self.engine.repo_candidates(q, limit=limit, root_ids=root_ids)
            return self._search_repo().repo_candidates(q, limit=limit, root_ids=root_ids)
        except Exception:
            return []

    def get_symbol_block(self, path: str, symbol_name: str) -> Optional[str]:
        try:
            loc = self._symbol_repo().get_symbol_range(path, symbol_name)
            if not loc:
                return None
            start, end = loc
            content = self.read_file(path)
            if not content:
                return None
            lines = content.splitlines()
            if 1 <= start <= len(lines):
                return "\n".join(lines[start - 1 : end])
            return None
        except Exception:
            return None

    def read_file_raw(self, path: str) -> Optional[str]:
        # Return raw content as text for tools expecting string.
        return self.read_file(path)

    def search_v2(self, opts: Any):
        if self.engine and hasattr(self.engine, "search_v2"):
            return self.engine.search_v2(opts)
        return self._search_repo().search_v2(opts)
    def read_file(self, path: str) -> Optional[str]:
        row = File.select(File.content).where(File.path == path).first()
        if not row: return None
        content = row.content
        if isinstance(content, bytes) and content.startswith(b"ZLIB\0"):
            try: content = zlib.decompress(content[5:])
            except: return None
        if isinstance(content, bytes): return content.decode("utf-8", errors="ignore")
        return str(content)

    def list_files(self, limit: int = 50, repo: Optional[str] = None, root_ids: Optional[List[str]] = None) -> List[Dict]:
        return self._file_repo().list_files(limit=limit, repo=repo, root_ids=root_ids)

    def has_legacy_paths(self) -> bool:
        """Check if database uses legacy path format (pre-multi-workspace)."""
        return False

    def update_last_seen_tx(self, cur, paths: List[str], ts: int) -> None:
        """Update last_seen timestamp for given paths."""
        self._file_repo(cur).update_last_seen_tx(cur, paths, ts)

    def get_repo_stats(self, root_ids: Optional[List[str]] = None) -> Dict[str, int]:
        return self._file_repo().get_repo_stats(root_ids=root_ids)

    def _file_repo(self, cur=None) -> "FileRepository":
        from sari.core.repository.file_repository import FileRepository
        conn = cur.connection if cur else self._read
        return FileRepository(conn)

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        return self._file_repo().get_file_meta(path)

    def close(self): self.db.close()
    def close_all(self): self.close()
    @property
    def _read(self):
        self._wait_for_swap()
        conn = self.db.connection(); conn.row_factory = sqlite3.Row
        return conn
    @property
    def _write(self):
        self._wait_for_swap()
        return self.db.connection()
    def _get_conn(self):
        self._wait_for_swap()
        return self._read
    def has_table_columns(self, table: str, columns: Iterable[str]) -> Tuple[bool, List[str]]:
        try:
            cur = self._read.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall() if row and len(row) > 1}
            missing = [c for c in columns if c not in existing]
            return len(missing) == 0, missing
        except Exception:
            return False, list(columns)
    def prune_stale_data(self, root_id: str, active_paths: List[str]):
        if not active_paths: return
        File.update(deleted_ts=int(time.time())).where(File.root_id == root_id, File.path.not_in(active_paths)).execute()

    def swap_db_file(self, snapshot_path: str) -> None:
        if not snapshot_path or not os.path.exists(snapshot_path):
            raise FileNotFoundError(f"snapshot db not found: {snapshot_path}")
        with self._lock:
            self._swap_in_progress.set()
            try:
                try:
                    self.db.close()
                except Exception:
                    pass
                # Cleanup stale WAL/SHM for target path (can cause I/O errors)
                for suffix in ("-wal", "-shm"):
                    try:
                        os.remove(self.db_path + suffix)
                    except FileNotFoundError:
                        pass
                # Cleanup snapshot WAL/SHM before swap
                for suffix in ("-wal", "-shm"):
                    try:
                        os.remove(snapshot_path + suffix)
                    except FileNotFoundError:
                        pass
                os.replace(snapshot_path, self.db_path)
                try:
                    self.db = SqliteDatabase(self.db_path, pragmas={
                        'journal_mode': 'wal', 'synchronous': 'normal',
                        'busy_timeout': 60000, 'foreign_keys': 1
                    }, check_same_thread=False)
                    self.db.connect(reuse_if_open=True)
                except Exception:
                    # Fallback to DELETE mode to avoid WAL-related I/O errors on some FS
                    self.db = SqliteDatabase(self.db_path, pragmas={
                        'journal_mode': 'delete', 'synchronous': 'normal',
                        'busy_timeout': 60000, 'foreign_keys': 1
                    }, check_same_thread=False)
                    self.db.connect(reuse_if_open=True)
                db_proxy.initialize(self.db)
                self._init_mem_staging()
            finally:
                self._swap_in_progress.clear()
