import sqlite3
import time
import logging

CURRENT_SCHEMA_VERSION = 3
logger = logging.getLogger("sari.db.schema")

def init_schema(conn: sqlite3.Connection):
    """Initialize database schema according to redesign standards."""
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
    if not cur.fetchone():
        _create_all_tables(cur)
        cur.execute("INSERT INTO schema_version (version, applied_ts) VALUES (?, ?)", 
                    (CURRENT_SCHEMA_VERSION, int(time.time())))
    else:
        cur.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        v = row[0] if row and isinstance(row[0], int) else 1
        
        # Migration to v2: importance_score
        if v < 2:
            try: cur.execute("ALTER TABLE symbols ADD COLUMN importance_score REAL DEFAULT 0.0")
            except Exception: pass
            
        # Migration to v3: stats columns and metadata_json check
        if v < 3:
            try:
                cur.execute("ALTER TABLE roots ADD COLUMN file_count INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE roots ADD COLUMN symbol_count INTEGER DEFAULT 0")
                cur.execute("CREATE TABLE IF NOT EXISTS meta_stats (key TEXT PRIMARY KEY, value TEXT, updated_ts INTEGER)")
            except Exception: pass
            
        # Hard check for metadata_json (resilience against messy refactoring)
        try:
            cur.execute("SELECT metadata_json FROM files LIMIT 1")
        except Exception:
            try: cur.execute("ALTER TABLE files ADD COLUMN metadata_json TEXT DEFAULT '{}'")
            except Exception: pass
            
        cur.execute("UPDATE schema_version SET version = ?", (CURRENT_SCHEMA_VERSION,))
    
    _init_fts(cur)

def _create_all_tables(cur: sqlite3.Cursor):
    """Create all database tables."""
    cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_ts INTEGER NOT NULL)")
    
    _create_roots_table(cur)
    _create_files_table(cur)
    _create_symbols_table(cur)
    _create_symbol_relations_table(cur)
    _create_contexts_table(cur)
    _create_snippets_table(cur)
    _create_failed_tasks_table(cur)
    _create_embeddings_table(cur)
    _create_meta_stats_table(cur)


def _create_roots_table(cur: sqlite3.Cursor):
    """Create roots table for workspace tracking."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roots (
            root_id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL,
            real_path TEXT,
            last_scan_ts INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            symbol_count INTEGER DEFAULT 0,
            config_json TEXT,
            label TEXT,
            state TEXT DEFAULT 'ready',
            created_ts INTEGER,
            updated_ts INTEGER
        );
    """)


def _create_files_table(cur: sqlite3.Cursor):
    """Create files table with indexes."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            rel_path TEXT NOT NULL,
            root_id TEXT NOT NULL,
            repo TEXT,
            mtime INTEGER NOT NULL,
            size INTEGER NOT NULL,
            content BLOB,
            hash TEXT,
            fts_content TEXT,
            last_seen_ts INTEGER DEFAULT 0,
            deleted_ts INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error TEXT,
            parse_status TEXT DEFAULT 'ok',
            parse_error TEXT,
            ast_status TEXT DEFAULT 'none',
            ast_reason TEXT DEFAULT 'none',
            is_binary INTEGER DEFAULT 0,
            is_minified INTEGER DEFAULT 0,
            metadata_json TEXT,
            FOREIGN KEY(root_id) REFERENCES roots(root_id)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_rel_path ON files(rel_path);")


def _create_symbols_table(cur: sqlite3.Cursor):
    """Create symbols table with indexes."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            symbol_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            root_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content TEXT,
            parent TEXT,
            meta_json TEXT,
            doc_comment TEXT,
            qualname TEXT,
            importance_score REAL DEFAULT 0.0,
            FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);")


def _create_symbol_relations_table(cur: sqlite3.Cursor):
    """Create symbol_relations table for tracking symbol dependencies."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_relations (
            from_path TEXT NOT NULL,
            from_root_id TEXT NOT NULL,
            from_symbol TEXT NOT NULL,
            from_symbol_id TEXT,
            to_path TEXT NOT NULL,
            to_root_id TEXT NOT NULL,
            to_symbol TEXT NOT NULL,
            to_symbol_id TEXT,
            rel_type TEXT NOT NULL,
            line INTEGER,
            meta_json TEXT
        );
    """)


def _create_contexts_table(cur: sqlite3.Cursor):
    """Create contexts table for storing contextual information."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT UNIQUE,
            content TEXT NOT NULL,
            tags_json TEXT,
            related_files_json TEXT,
            source TEXT,
            valid_from INTEGER,
            valid_until INTEGER,
            deprecated INTEGER DEFAULT 0,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL
        );
    """)


def _create_snippets_table(cur: sqlite3.Cursor):
    """Create snippets table for code snippet storage."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            path TEXT NOT NULL,
            root_id TEXT NOT NULL,
            start_line INTEGER,
            end_line INTEGER,
            content TEXT,
            content_hash TEXT,
            anchor_before TEXT,
            anchor_after TEXT,
            repo TEXT,
            note TEXT,
            commit_hash TEXT,
            created_ts INTEGER NOT NULL,
            updated_ts INTEGER NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY(root_id) REFERENCES roots(root_id),
            UNIQUE(tag, root_id, path, start_line, end_line)
        );
    """)


def _create_failed_tasks_table(cur: sqlite3.Cursor):
    """Create failed_tasks table for retry tracking."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS failed_tasks (
            path TEXT PRIMARY KEY,
            root_id TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            error TEXT,
            ts INTEGER,
            next_retry INTEGER,
            metadata_json TEXT,
            FOREIGN KEY(root_id) REFERENCES roots(root_id)
        );
    """)


def _create_embeddings_table(cur: sqlite3.Cursor):
    """Create embeddings table for vector storage."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            root_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            content_hash TEXT,
            model TEXT,
            vector BLOB,
            created_ts INTEGER,
            updated_ts INTEGER,
            PRIMARY KEY(root_id, entity_type, entity_id)
        );
    """)


def _create_meta_stats_table(cur: sqlite3.Cursor):
    """Create meta_stats table for metadata storage."""
    cur.execute("CREATE TABLE IF NOT EXISTS meta_stats (key TEXT PRIMARY KEY, value TEXT, updated_ts INTEGER)")


def _init_fts(cur: sqlite3.Cursor):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'")
    if cur.fetchone(): return

    cur.execute("CREATE VIRTUAL TABLE files_fts USING fts5(path, rel_path, fts_content, content='files', content_rowid='rowid')")
    
    cur.execute("CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN INSERT INTO files_fts(rowid, path, rel_path, fts_content) VALUES (new.rowid, new.path, new.rel_path, new.fts_content); END")
    cur.execute("CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, path, rel_path, fts_content) VALUES('delete', old.rowid, old.path, old.rel_path, old.fts_content); END")
    cur.execute("CREATE TRIGGER files_au AFTER UPDATE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, path, rel_path, fts_content) VALUES('delete', old.rowid, old.path, old.rel_path, old.fts_content); INSERT INTO files_fts(rowid, path, rel_path, fts_content) VALUES (new.rowid, new.path, new.rel_path, new.fts_content); END")