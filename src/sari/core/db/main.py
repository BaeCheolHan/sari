import os
import logging
from typing import List, Dict, Any, Optional
from peewee import SqliteDatabase
from .models import db_proxy, File, Symbol, Relation, Root
from sari.core.settings import settings

logger = logging.getLogger("sari.db")

try:
    import tantivy
    HAS_TANTIVY = True
except ImportError:
    logger.error("❌ Critical: tantivy not found!")
    HAS_TANTIVY = False

class LocalSearchDB:
    def __init__(self, db_path: str):
        self.db = SqliteDatabase(db_path, pragmas={
            'journal_mode': 'wal',
            'cache_size': -1 * 64000,
            'foreign_keys': 1,
            'busy_timeout': 10000,
            'synchronous': 'normal'
        })
        db_proxy.initialize(self.db)
        self.db.create_tables([File, Symbol, Relation, Root])
        
        self.engine = None
        if HAS_TANTIVY:
            from sari.core.engine.tantivy_engine import TantivyEngine
            idx_path = os.path.join(os.path.dirname(db_path), "tantivy_index")
            self.engine = TantivyEngine(idx_path)

    def upsert_files_turbo(self, batch: List[Dict[str, Any]]):
        with self.db.atomic():
            for task in batch:
                File.insert(
                    path=task["rel"],
                    repo=task.get("repo", ""),
                    content=task.get("content", ""),
                    content_hash=task.get("content_hash", ""),
                    size=task.get("size", 0),
                    mtime=task.get("mtime", 0),
                    scan_ts=task.get("scan_ts", 0),
                    parse_status=task.get("parse_status", "ok"),
                    ast_status=task.get("ast_status", "ok"),
                    is_binary=task.get("is_binary", 0),
                    is_minified=task.get("is_minified", 0),
                    metadata_json=task.get("metadata_json", "{}")
                ).on_conflict_replace().execute()

    def finalize_turbo_batch(self):
        """Force commit and checkpoint for SQLite."""
        self.db.commit()
        self.db.execute_sql("PRAGMA wal_checkpoint(FULL)")
        if self.engine:
            self.engine.commit()

    def search_files(self, query: str, limit: int = 10) -> List[Dict]:
        if not self.engine:
            # Fallback to SQLite if engine is missing
            logger.warning("Search engine missing, falling back to SQLite LIKE.")
            files = File.select().where(File.content.contains(query)).limit(limit)
            return [{"path": f.path, "repo": f.repo} for f in files]
        return self.engine.search(query, limit=limit)

    def read_file(self, path: str) -> Optional[Dict]:
        try:
            f = File.get(File.path == path)
            return {"path": f.path, "content": f.content}
        except: return None

    def list_files(self, limit: int = 10) -> List[Dict]:
        files = File.select().limit(limit)
        return [{"path": f.path, "size": f.size, "repo": f.repo} for f in files]

    def preload_metadata(self): pass
    def prune_stale_files(self, ts): pass
