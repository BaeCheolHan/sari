import sqlite3
import time
import logging

CURRENT_SCHEMA_VERSION = 5
logger = logging.getLogger("sari.db.schema")


def _row_get(row, key: str, index: int, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return default


def init_schema(conn: sqlite3.Connection):
    """Sari의 데이터베이스 스키마를 최신 표준에 맞게 초기화하고 마이그레이션을 관리합니다."""
    cur = conn.cursor()

    # 스키마 버전 확인 및 초기 생성
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
    if not cur.fetchone():
        _create_all_tables(cur)
        cur.execute("INSERT INTO schema_version (version, applied_ts) VALUES (?, ?)",
                    (CURRENT_SCHEMA_VERSION, int(time.time())))
    else:
        cur.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        v = int(_row_get(row, "version", 0, 1) or 1)

        # v2 마이그레이션: 중요도 점수(importance_score) 컬럼 추가
        if v < 2:
            try:
                cur.execute(
                    "ALTER TABLE symbols ADD COLUMN importance_score REAL DEFAULT 0.0")
            except Exception as e:
                logger.debug(
                    "Migration v2 column already exists or failed: %s", e)

        # v3 마이그레이션: 통계 컬럼 및 메타 통계 테이블 추가
        if v < 3:
            try:
                cur.execute(
                    "ALTER TABLE roots ADD COLUMN file_count INTEGER DEFAULT 0")
                cur.execute(
                    "ALTER TABLE roots ADD COLUMN symbol_count INTEGER DEFAULT 0")
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS meta_stats (key TEXT PRIMARY KEY, value TEXT, updated_ts INTEGER)")
            except Exception as e:
                logger.debug("Migration v3 failed: %s", e)

        # v4 마이그레이션: snippet_versions 테이블 추가
        if v < 4:
            try:
                _create_snippet_versions_table(cur)
            except Exception as e:
                logger.debug("Migration v4 failed: %s", e)

        # v5 마이그레이션: symbol_relations 중복 제거 + 유니크 인덱스 추가
        if v < 5:
            try:
                _deduplicate_symbol_relations(cur)
                _create_symbol_relations_indexes(cur)
            except Exception as e:
                logger.debug("Migration v5 failed: %s", e)

        # metadata_json 컬럼 존재 여부 강제 확인 (복구용)
        try:
            cur.execute("SELECT metadata_json FROM files LIMIT 1")
        except Exception:
            try:
                cur.execute(
                    "ALTER TABLE files ADD COLUMN metadata_json TEXT DEFAULT '{}'")
            except Exception as e:
                logger.debug("Failed to add metadata_json column: %s", e)

        # snippet_versions 테이블 존재 여부 강제 확인 (복구용)
        try:
            cur.execute("SELECT id FROM snippet_versions LIMIT 1")
        except Exception:
            try:
                _create_snippet_versions_table(cur)
            except Exception as e:
                logger.debug("Failed to create snippet_versions table: %s", e)

        cur.execute("UPDATE schema_version SET version = ?",
                    (CURRENT_SCHEMA_VERSION,))

    _init_fts(cur)


def _create_all_tables(cur: sqlite3.Cursor):
    """데이터베이스의 모든 테이블을 생성합니다."""
    # 스키마 버전 관리 테이블
    cur.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_ts INTEGER NOT NULL)")

    _create_roots_table(cur)
    _create_files_table(cur)
    _create_symbols_table(cur)
    _create_symbol_relations_table(cur)
    _create_contexts_table(cur)
    _create_snippets_table(cur)
    _create_snippet_versions_table(cur)
    _create_failed_tasks_table(cur)
    _create_embeddings_table(cur)
    _create_meta_stats_table(cur)


def _create_roots_table(cur: sqlite3.Cursor):
    """워크스페이스 루트 정보를 관리하는 'roots' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roots (
            root_id TEXT PRIMARY KEY,    -- 워크스페이스 고유 식별자 (해시 등)
            root_path TEXT NOT NULL,     -- 워크스페이스 원본 경로
            real_path TEXT,              -- 실제 물리적 경로
            last_scan_ts INTEGER DEFAULT 0, -- 마지막 스캔 시간 (Unix Timestamp)
            file_count INTEGER DEFAULT 0,   -- 인덱싱된 파일 수
            symbol_count INTEGER DEFAULT 0, -- 추출된 심볼 수
            config_json TEXT,            -- 워크스페이스별 설정 (JSON)
            label TEXT,                  -- 사용자에게 보여줄 이름
            state TEXT DEFAULT 'ready',  -- 현재 상태 (ready, indexing 등)
            created_ts INTEGER,          -- 생성 일시
            updated_ts INTEGER           -- 수정 일시
        );
    """)


def _create_files_table(cur: sqlite3.Cursor):
    """인덱싱된 파일 정보를 관리하는 'files' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,       -- 파일 절대 경로 (식별자)
            rel_path TEXT NOT NULL,      -- 워크스페이스 루트 기준 상대 경로
            root_id TEXT NOT NULL,       -- 소속된 워크스페이스 ID
            repo TEXT,                   -- 소속 저장소 이름 (옵션)
            mtime INTEGER NOT NULL,      -- 파일 수정 시간
            size INTEGER NOT NULL,       -- 파일 크기 (바이트)
            content BLOB,                -- 파일 내용 (선택적 저장/압축 가능)
            hash TEXT,                   -- 파일 내용 해시 (변경 감지용)
            fts_content TEXT,            -- 전체 텍스트 검색(FTS)용 텍스트
            last_seen_ts INTEGER DEFAULT 0, -- 스캔 중 마지막으로 발견된 시간
            deleted_ts INTEGER DEFAULT 0,   -- 삭제된 경우 삭제 일시 (Soft Delete)
            status TEXT DEFAULT 'ok',    -- 분석 상태 (ok, error 등)
            error TEXT,                  -- 분석 중 발생한 오류 내용
            parse_status TEXT DEFAULT 'ok', -- 파싱 상태
            parse_error TEXT,            -- 파싱 오류 내용
            ast_status TEXT DEFAULT 'none', -- AST 추출 상태
            ast_reason TEXT DEFAULT 'none', -- 상태 변화 사유
            is_binary INTEGER DEFAULT 0, -- 이진 파일 여부 (1: true)
            is_minified INTEGER DEFAULT 0, -- 압축(Minified) 파일 여부 (1: true)
            metadata_json TEXT,          -- 추가 메타데이터 (JSON)
            FOREIGN KEY(root_id) REFERENCES roots(root_id)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_id);")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_files_rel_path ON files(rel_path);")


def _create_symbols_table(cur: sqlite3.Cursor):
    """파일 내 소스코드 심볼(함수, 클래스 등)을 관리하는 'symbols' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            symbol_id TEXT PRIMARY KEY,  -- 심볼 고유 ID
            path TEXT NOT NULL,          -- 소속 파일 경로
            root_id TEXT NOT NULL,       -- 소속 워크스페이스 ID
            name TEXT NOT NULL,          -- 심볼 이름
            kind TEXT NOT NULL,          -- 심볼 종류 (function, class 등)
            line INTEGER NOT NULL,       -- 정의 시작 라인 (1-based)
            end_line INTEGER NOT NULL,   -- 정의 종료 라인
            content TEXT,                -- 심볼 소스코드 부분
            parent TEXT,                 -- 부모 심볼 이름 (계층 구조)
            meta_json TEXT,              -- 인자 정보 등 추가 메타데이터 (JSON)
            doc_comment TEXT,            -- 관련 문서 주석
            qualname TEXT,               -- 정규화된 이름 (Full Name)
            importance_score REAL DEFAULT 0.0, -- 분석 기반 중요도 점수
            FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);")


def _create_symbol_relations_table(cur: sqlite3.Cursor):
    """심볼 간의 상호 참조 관계(호출, 상속 등)를 관리하는 'symbol_relations' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS symbol_relations (
            from_path TEXT NOT NULL,     -- 호출/참조하는 파일
            from_root_id TEXT NOT NULL,
            from_symbol TEXT NOT NULL,   -- 호출/참조하는 심볼 이름
            from_symbol_id TEXT,         -- 호출/참조하는 심볼 ID (옵션)
            to_path TEXT NOT NULL,       -- 호출/참조 대상 파일
            to_root_id TEXT NOT NULL,
            to_symbol TEXT NOT NULL,     -- 호출/참조 대상 심볼 이름
            to_symbol_id TEXT,           -- 호출/참조 대상 심볼 ID (옵션)
            rel_type TEXT NOT NULL,      -- 관계 종류 (call, inheritance 등)
            line INTEGER,                -- 관계가 발생한 소스 라인
            meta_json TEXT               -- 추가 정보 (JSON)
        );
    """)
    _create_symbol_relations_indexes(cur)


def _create_symbol_relations_indexes(cur: sqlite3.Cursor):
    # Identity-level unique index to prevent repeated accumulation.
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_symbol_relations_identity
        ON symbol_relations(
            from_path,
            from_root_id,
            from_symbol,
            IFNULL(from_symbol_id, ''),
            to_path,
            to_root_id,
            to_symbol,
            IFNULL(to_symbol_id, ''),
            rel_type,
            IFNULL(line, -1),
            IFNULL(meta_json, '')
        )
        """
    )
    # Query helpers for call graph lookups.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_symbol_relations_to_symbol ON symbol_relations(to_symbol)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_symbol_relations_to_symbol_id ON symbol_relations(to_symbol_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_symbol_relations_from_symbol ON symbol_relations(from_symbol)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_symbol_relations_from_symbol_id ON symbol_relations(from_symbol_id)")


def _deduplicate_symbol_relations(cur: sqlite3.Cursor):
    cur.execute(
        """
        DELETE FROM symbol_relations
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM symbol_relations
            GROUP BY
                from_path,
                from_root_id,
                from_symbol,
                IFNULL(from_symbol_id, ''),
                to_path,
                to_root_id,
                to_symbol,
                IFNULL(to_symbol_id, ''),
                rel_type,
                IFNULL(line, -1),
                IFNULL(meta_json, '')
        )
        """
    )


def _create_contexts_table(cur: sqlite3.Cursor):
    """지적 맥락 정보(Topic 기반 지식 등)를 관리하는 'contexts' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT UNIQUE,           -- 주제/컨텍스트 키워드
            content TEXT NOT NULL,       -- 컨텍스트 본문
            tags_json TEXT,              -- 관련 태그들 (JSON Array)
            related_files_json TEXT,     -- 연관 파일 정보 (JSON Array)
            source TEXT,                 -- 정보 출처
            valid_from INTEGER,          -- 유효 시작 일시
            valid_until INTEGER,         -- 유효 종료 일시
            deprecated INTEGER DEFAULT 0, -- 폐기 여부 (1: true)
            created_ts INTEGER NOT NULL, -- 생성 일시
            updated_ts INTEGER NOT NULL  -- 수정 일시
        );
    """)


def _create_snippets_table(cur: sqlite3.Cursor):
    """코드 스니펫과 관련 메모를 관리하는 'snippets' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,           -- 스니펫 분류/태그
            path TEXT NOT NULL,          -- 소속 파일 경로
            root_id TEXT NOT NULL,       -- 소속 워크스페이스 ID
            start_line INTEGER,          -- 시작 라인
            end_line INTEGER,            -- 종료 라인
            content TEXT,                -- 스니펫 본문 소스
            content_hash TEXT,           -- 본문 해시
            anchor_before TEXT,          -- 주변 맥락 추적용 이전 텍스트
            anchor_after TEXT,           -- 주변 맥락 추적용 이후 텍스트
            repo TEXT,                   -- 소속 저장소
            note TEXT,                   -- 사용자 메모/설명
            commit_hash TEXT,            -- 생성 당시 커밋 해시
            created_ts INTEGER NOT NULL, -- 생성 일시
            updated_ts INTEGER NOT NULL, -- 수정 일시
            metadata_json TEXT,          -- 추가 메타데이터 (JSON)
            FOREIGN KEY(root_id) REFERENCES roots(root_id),
            UNIQUE(tag, root_id, path, start_line, end_line)
        );
    """)


def _create_snippet_versions_table(cur: sqlite3.Cursor):
    """스니펫 이력 관리를 위한 'snippet_versions' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snippet_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snippet_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT,
            created_ts INTEGER NOT NULL,
            FOREIGN KEY(snippet_id) REFERENCES snippets(id) ON DELETE CASCADE
        );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_snippet_versions_snippet_id ON snippet_versions(snippet_id);")


def _create_failed_tasks_table(cur: sqlite3.Cursor):
    """인덱싱 실패 시 재시도 대기를 위한 'failed_tasks' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS failed_tasks (
            path TEXT PRIMARY KEY,       -- 실패한 파일 경로
            root_id TEXT NOT NULL,       -- 소속 워크스페이스 ID
            attempts INTEGER DEFAULT 0,   -- 지금까지의 재시도 횟수
            error TEXT,                  -- 마지막 에러 메시지
            ts INTEGER,                  -- 발생 일시
            next_retry INTEGER,          -- 다음 재시도 예정 일시
            metadata_json TEXT,          -- 작업 관련 추가 정보 (JSON)
            FOREIGN KEY(root_id) REFERENCES roots(root_id)
        );
    """)


def _create_embeddings_table(cur: sqlite3.Cursor):
    """벡터 검색을 위한 임베딩 데이터를 관리하는 'embeddings' 테이블을 생성합니다."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            root_id TEXT NOT NULL,       -- 소속 워크스페이스 ID
            entity_type TEXT NOT NULL,   -- 엔티티 종류 (file, symbol 등)
            entity_id TEXT NOT NULL,     -- 엔티티 ID
            content_hash TEXT,           -- 원본 내용 해시
            model TEXT,                  -- 사용된 모델 이름
            vector BLOB,                 -- 하드웨어 최적화된 벡터 데이터
            created_ts INTEGER,          -- 생성 일시
            updated_ts INTEGER,          -- 수정 일시
            PRIMARY KEY(root_id, entity_type, entity_id)
        );
    """)


def _create_meta_stats_table(cur: sqlite3.Cursor):
    """시스템 메타데이터 및 통계를 보관하는 'meta_stats' 테이블을 생성합니다."""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS meta_stats (key TEXT PRIMARY KEY, value TEXT, updated_ts INTEGER)")


def _init_fts(cur: sqlite3.Cursor):
    """SQLite FTS5를 이용한 고속 텍스트 검색을 초기화합니다."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'")
    if cur.fetchone():
        return

    # 외부 콘텐츠(content='files')를 사용하는 가상 테이블 생성
    cur.execute(
        "CREATE VIRTUAL TABLE files_fts USING fts5(path, rel_path, fts_content, content='files', content_rowid='rowid')")

    # files 테이블 변경 시 fts 테이블 자동 동기화를 위한 트리거들
    cur.execute("CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN INSERT INTO files_fts(rowid, path, rel_path, fts_content) VALUES (new.rowid, new.path, new.rel_path, new.fts_content); END")
    cur.execute("CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, path, rel_path, fts_content) VALUES('delete', old.rowid, old.path, old.rel_path, old.fts_content); END")
    cur.execute("CREATE TRIGGER files_au AFTER UPDATE ON files BEGIN INSERT INTO files_fts(files_fts, rowid, path, rel_path, fts_content) VALUES('delete', old.rowid, old.path, old.rel_path, old.fts_content); INSERT INTO files_fts(rowid, path, rel_path, fts_content) VALUES (new.rowid, new.path, new.rel_path, new.fts_content); END")
