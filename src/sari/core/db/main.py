import sqlite3
import logging
import time
import zlib
import threading
from typing import List, Dict, Any, Optional, Iterable, Tuple

try:
    from peewee import SqliteDatabase, fn, CompositeKey, ForeignKeyField, Proxy, Model, CharField, TextField, IntegerField, BigIntegerField, BlobField, AutoField
    from .models import database_proxy, Root, File, Symbol, FailedTask, Snippet, Context
    HAS_PEEWEE = True
except ImportError:
    HAS_PEEWEE = False
    class SqliteDatabase:
        def __init__(self, *args, **kwargs): pass
        def connect(self, **kwargs): pass
        def atomic(self):
            class Atom:
                def __enter__(self): return self
                def __exit__(self, *args): pass
            return Atom()
    def fn(*args): pass
    database_proxy = None

from .schema import init_schema

class LocalSearchDB:
    def __init__(self, db_path: str, logger=None):
        self.db_path = db_path
        self.logger = logger or logging.getLogger("sari.db")
        self.engine = None
        self.coordinator = None
        self._lock = threading.Lock() # Fix for archive_context
        
        # Phase 11: Telemetry Shim for resilience in MCP tools
        if not hasattr(self.logger, "log_telemetry"):
            try:
                setattr(self.logger, "log_telemetry", lambda msg: self.logger.info(f"TELEMETRY: {msg}"))
            except (AttributeError, TypeError): pass

        if not HAS_PEEWEE:
            self.logger.warning("Peewee not installed. DB functions will be limited.")
            return

        self.db = SqliteDatabase(self.db_path, pragmas={
            'journal_mode': 'wal',
            'synchronous': 'normal',
            'busy_timeout': 10000,    # Priority 4: Prevent "database is locked"
            'mmap_size': 30000000000,
            'page_size': 65536,
            'cache_size': -100000,    # 100MB cache
            'foreign_keys': 1,
            'temp_store': 'memory'    # Priority 4: Faster temp tables
        })
        # Important for multi-threaded use with peewee
        self.db.connect(reuse_if_open=True)
        database_proxy.initialize(self.db)
        # Priority 10: Safe migration check
        self._check_and_migrate()
        
        with self.db.atomic():
            init_schema(self.db.connection())
        
        self._init_mem_staging()

    def _check_and_migrate(self):
        """Check schema version and perform migration or safe backup/re-create."""
        try:
            # Simple version check using a custom PRAGMA or dedicated table
            cursor = self.db.execute_sql("PRAGMA user_version")
            current_version = cursor.fetchone()[0]
            
            target_version = 2 # Current Registry/Schema version
            
            if current_version < target_version:
                if current_version == 0:
                    # New DB, just set version
                    self.db.execute_sql(f"PRAGMA user_version = {target_version}")
                else:
                    # Priority 10 Fix: Safe Backup instead of deletion
                    self.db.close()
                    backup_path = f"{self.db_path}.bak.{int(time.time())}"
                    os.rename(self.db_path, backup_path)
                    if self.logger: self.logger.info(f"Schema mismatch. Old DB backed up to {backup_path}")
                    # Re-open will trigger schema init
                    self.db.connect(reuse_if_open=True)
                    self.db.execute_sql(f"PRAGMA user_version = {target_version}")
        except Exception as e:
            if self.logger: self.logger.error(f"Migration check failed: {e}")

    def set_engine(self, engine):
        self.engine = engine

    def _init_mem_staging(self):
        try:
            conn = self.db.connection()
            conn.execute("ATTACH DATABASE ':memory:' AS staging_mem")
            conn.execute("CREATE TABLE IF NOT EXISTS staging_mem.files_temp AS SELECT * FROM main.files WHERE 0")
        except sqlite3.OperationalError: pass

    def ensure_root(self, root_id: str, path: str):
        with self.db.atomic():
            Root.insert(
                root_id=root_id, 
                root_path=path, 
                real_path=path,
                label=path.split("/")[-1]
            ).on_conflict_ignore().execute()
        self.db.commit()

    def upsert_files_turbo(self, rows: Iterable[tuple]):
        conn = self.db.connection()
        try:
            placeholders = ",".join(["?"] * 20)
            conn.executemany(f"INSERT OR REPLACE INTO staging_mem.files_temp VALUES ({placeholders})", rows)
        except sqlite3.OperationalError:
            self._init_mem_staging()
            conn.executemany(f"INSERT OR REPLACE INTO staging_mem.files_temp VALUES ({placeholders})", rows)

    def finalize_turbo_batch(self):
        conn = self.db.connection()
        try:
            conn.execute("BEGIN")
            conn.execute("INSERT OR REPLACE INTO main.files SELECT * FROM staging_mem.files_temp")
            conn.execute("DELETE FROM staging_mem.files_temp")
            conn.commit()
        except:
            conn.rollback()

    def upsert_symbols_tx(self, cur, rows: List[tuple], root_id: str = "root"):
        if not rows: return
        conn = self.db.connection()
        self.ensure_root(root_id, root_id)
        
        mapped_rows = []
        for r in rows:
            if not isinstance(r, (list, tuple)) or len(r) < 11: continue
            mapped = (r[10], r[0], root_id, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9])
            mapped_rows.append(mapped)
            
        if not mapped_rows: return
        placeholders = ",".join(["?"] * 12)
        try:
            with self.db.atomic():
                conn.executemany(f"INSERT OR REPLACE INTO symbols VALUES ({placeholders})", mapped_rows)
        except Exception as e:
            if self.logger: self.logger.error(f"Failed to upsert symbols: {e}")

    def get_all_file_metadata(self, root_id: str) -> Dict[str, Tuple[int, int, str]]:
        """Priority 3: Preload all file metadata for a root to avoid 4000+ individual SELECTs."""
        # SQLite execution
        cursor = self.db.execute_sql(
            "SELECT path, mtime, size, content_hash FROM files WHERE root_id = ? AND deleted_ts = 0", 
            (root_id,)
        )
        return {r[0]: (r[1], r[2], r[3]) for r in cursor.fetchall()}

    def get_unseen_paths(self, root_id: str, scan_ts: int) -> List[str]:
        """Find paths that were NOT seen in the current scan session."""
        cursor = self.db.execute_sql(
            "SELECT path FROM files WHERE root_id = ? AND last_seen_ts < ? AND deleted_ts = 0",
            (root_id, scan_ts)
        )
        return [r[0] for r in cursor.fetchall()]

    def update_last_seen_batch(self, paths: Iterable[str], scan_ts: int):
        """Batch update last_seen_ts for performance."""
        if not paths: return
        conn = self.db.connection()
        conn.executemany(
            "UPDATE files SET last_seen_ts = ? WHERE path = ?",
            [(scan_ts, p) for p in paths]
        )
        conn.commit()

    def read_file(self, path: str) -> Optional[str]:
        row = File.select(File.content).where(File.path == path).first()
        if not row: return None
        data = row.content
        if isinstance(data, bytes) and data.startswith(b"ZLIB\0"):
            return zlib.decompress(data[5:]).decode("utf-8", errors="ignore")
        return data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data

    def search_files(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        lq = f"%{query}%"
        return list(File.select().where((File.path ** lq) | (File.rel_path ** lq) | (File.content ** lq)).limit(limit).dicts())

    def list_files(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.db.execute_sql("SELECT path, size, repo FROM files LIMIT ?", (limit,))
        return [{"path": r[0], "size": r[1], "repo": r[2]} for r in cursor.fetchall()]

    def mark_deleted(self, path: str):
        ts = int(time.time())
        conn = self.db.connection()
        conn.execute("UPDATE files SET deleted_ts = ?, mtime = ? WHERE path = ?", (ts, ts, path))
        conn.execute("DELETE FROM symbols WHERE path = ?", (path,))
        conn.commit()

    def get_repo_stats(self, root_ids: List[str] = None) -> Dict[str, int]:
        query = File.select(File.repo, fn.COUNT(File.path).alias('count')).where(File.deleted_ts == 0)
        if root_ids: query = query.where(File.root_id.in_(root_ids))
        return {r['repo']: r['count'] for r in query.group_by(File.repo).dicts()}

    def get_roots(self) -> List[Dict[str, str]]:
        return list(Root.select().dicts())

    def has_legacy_paths(self) -> bool: return False
    def close(self): self.db.close()