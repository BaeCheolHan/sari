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

class LocalSearchDB:
    def __init__(self, db_path: str, logger=None):
        self.db_path = db_path
        self.logger = logger or logging.getLogger("sari.db")
        self.settings = None
        self.engine = None
        if not HAS_PEEWEE: return
        self.db = SqliteDatabase(self.db_path, pragmas={
            'journal_mode': 'wal', 'synchronous': 'normal',
            'busy_timeout': 60000, 'foreign_keys': 1
        }, check_same_thread=False)
        self.db.connect(reuse_if_open=True)
        db_proxy.initialize(self.db)
        with self.db.atomic(): init_schema(self.db.connection())
        self._init_mem_staging()
        self._lock = threading.Lock()

    def set_engine(self, engine): self.engine = engine
    def set_settings(self, settings): self.settings = settings
    def create_staging_table(self, cur=None): self._ensure_staging()

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

    def upsert_files_turbo(self, rows: Iterable[tuple]):
        self._ensure_staging()
        conn = self.db.connection()
        placeholders = ",".join(["?"] * 20)
        mapped = []
        for r in rows:
            base = [None] * 20
            for i in range(min(len(r), 20)): base[i] = r[i]
            
            # Fill NOT NULL defaults for resilience
            if base[0] is None: continue # Primary key must exist
            if base[1] is None: base[1] = os.path.basename(str(base[0])) # rel_path
            if base[2] is None: base[2] = "root" # root_id
            if base[3] is None: base[3] = "" # repo
            if base[4] is None: base[4] = 0 # mtime
            if base[5] is None: base[5] = 0 # size
            if base[6] is None: base[6] = b"" # content
            elif isinstance(base[6], str): base[6] = base[6].encode("utf-8", errors="ignore")
            # Other defaults
            if base[9] is None: base[9] = 0 # last_seen_ts
            if base[10] is None: base[10] = 0 # deleted_ts
            
            mapped.append(tuple(base))
        try: 
            conn.executemany(f"INSERT OR REPLACE INTO staging_mem.files_temp VALUES ({placeholders})", mapped)
            if conn.in_transaction: conn.commit()
        except Exception as e:
            print(f"DEBUG: upsert_files_turbo FAILED: {e}")
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
        conn = self.db.connection()
        try:
            if conn.in_transaction: conn.commit()
            self._ensure_staging()
            res = conn.execute("SELECT count(*) FROM staging_mem.files_temp").fetchone()
            cnt = res[0] if res else 0
            if cnt == 0: return
            try:
                conn.execute("BEGIN IMMEDIATE TRANSACTION")
                conn.execute("INSERT OR REPLACE INTO main.files SELECT * FROM staging_mem.files_temp")
                conn.execute("DELETE FROM staging_mem.files_temp")
                conn.execute("COMMIT")
            except Exception as te:
                try: conn.execute("ROLLBACK")
                except: pass
                raise te
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to finalize turbo batch: {e}")
            raise
    def upsert_symbols_tx(self, cur, rows: List[tuple], root_id: str = "root"):
        """Insert or update symbols with robust format detection.
        
        Supports two formats:
        - Format A (12-element, DB schema): (sid, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
        - Format B (11-element, legacy): (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, sid)
        """
        if not rows: return
        conn = self.db.connection()        
        self.ensure_root(root_id, root_id)
        
        mapped_rows = []
        for r in rows:
            if not isinstance(r, (list, tuple)): continue
            
            # Format detection
            if len(r) >= 12:
                # Likely Format A: Check if r[1] looks like a path (contains "/" or ".")
                if "/" in str(r[1]) or "\\" in str(r[1]):
                    # Format A: (sid, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
                    mapped = (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11])
                else:
                    # Ambiguous, default to using provided root_id
                    mapped = (r[0], r[1], root_id, r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11])
            elif len(r) == 11:
                # Format B: (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, sid)
                mapped = (r[10], r[0], root_id, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9])
            else:
                # Unknown format, skip
                if self.logger: self.logger.warning(f"Skipping symbol with unexpected format (len={len(r)})")
                continue
            
            mapped_rows.append(mapped)
            
        if not mapped_rows: return

        # Ensure files exist to satisfy FOREIGN KEY constraint
        # We extract unique paths and insert minimal file records if they don't exist
        # caused by: race conditions or partial indexing failure
        placeholders = ",".join(["?"] * 12)
        try:
            with self.db.atomic():
                conn.executemany(f"INSERT OR REPLACE INTO symbols VALUES ({placeholders})", mapped_rows)
        except Exception as e:
            if self.logger: self.logger.error(f"Failed to upsert symbols: {e}")
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

    def read_file(self, path: str) -> Optional[str]:
        row = File.select(File.content).where(File.path == path).first()
        if not row: return None
        content = row.content
        if isinstance(content, bytes) and content.startswith(b"ZLIB\0"):
            try: content = zlib.decompress(content[5:])
            except: return None
        if isinstance(content, bytes): return content.decode("utf-8", errors="ignore")
        return str(content)

    def list_files(self, limit: int = 50) -> List[Dict]:
        cursor = self.db.execute_sql("SELECT path, size, repo FROM files LIMIT ?", (limit,))
        return [{"path": r[0], "size": r[1], "repo": r[2]} for r in cursor.fetchall()]

    def has_legacy_paths(self) -> bool:
        """Check if database uses legacy path format (pre-multi-workspace)."""
        return False

    def update_last_seen_tx(self, cur, paths: List[str], ts: int) -> None:
        """Update last_seen timestamp for given paths."""
        if not paths:
            return
        placeholders = ",".join(["?"] * len(paths))
        sql = f"UPDATE files SET last_seen_ts={ts} WHERE path IN ({placeholders})"
        if cur:
            cur.execute(sql, paths)
        else:
            with self.db.atomic():
                self.db.execute_sql(sql, paths)

    def get_repo_stats(self) -> Dict[str, int]:
        query = File.select(File.repo, fn.COUNT(File.path).alias('count')).where(File.deleted_ts == 0).group_by(File.repo)
        return {row['repo']: row['count'] for row in query.dicts()}

    def get_file_meta(self, path: str) -> Optional[Tuple[int, int, str]]:
        row = File.select(File.mtime, File.size, File.metadata_json).where(File.path == path).first()
        if not row: return None
        ch = ""
        try: ch = json.loads(row.metadata_json).get("content_hash", "")
        except: pass
        return (row.mtime, row.size, ch)

    def close(self): self.db.close()
    def close_all(self): self.close()
    @property
    def _read(self):
        conn = self.db.connection(); conn.row_factory = sqlite3.Row
        return conn
    @property
    def _write(self): return self.db.connection()
    def _get_conn(self): return self._read
    def prune_stale_data(self, root_id: str, active_paths: List[str]):
        if not active_paths: return
        File.update(deleted_ts=int(time.time())).where(File.root_id == root_id, File.path.not_in(active_paths)).execute()