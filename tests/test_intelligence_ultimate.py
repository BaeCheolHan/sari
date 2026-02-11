import struct
from sari.mcp.tools.call_graph import build_call_graph
from sari.core.models import SearchOptions, IndexingResult


def _prepare_db_v2(conn):
    """테스트용 DB에 v2 스키마(importance_score) 강제 적용"""
    try:
        conn.execute(
            "ALTER TABLE symbols ADD COLUMN importance_score REAL DEFAULT 0.0")
    except Exception:
        pass


def test_intelligence_fuzzy_autorun(db, workspace):
    """오타가 포함된 심볼 조회 시 Fuzzy 알고리즘 작동 검증"""
    rid = workspace
    conn = db.get_read_connection()
    _prepare_db_v2(conn)

    res = IndexingResult(
        path=f"{rid}/user.py",
        rel="user.py",
        root_id=rid,
        repo="repo",
        type="new")
    db.upsert_files_turbo([res.to_file_row()])
    db.finalize_turbo_batch()

    conn.execute(
        "INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname) VALUES (?,?,?,?,?,?,?,?,?)",
        ("s1",
         f"{rid}/user.py",
         rid,
         "UserService",
         "class",
         1,
         10,
         "",
         "UserService"))

    graph = build_call_graph({"name": "UserServcie", "depth": 1}, db, [rid])
    assert graph["symbol"] == "UserService"
    assert "fuzzy match" in graph.get("scope_reason", "").lower()


def test_intelligence_centrality_ranking(db, workspace):
    """중요도(Centrality) 알고리즘이 랭킹에 미치는 영향 검증"""
    rid = workspace
    conn = db.get_read_connection()
    _prepare_db_v2(conn)

    # 1. 두 개의 파일 준비 (fts_content에 공통 키워드 'core' 주입)
    # File A: 중요도 높음
    row_a = IndexingResult(
        path=f"{rid}/engine.py",
        rel="engine.py",
        root_id=rid,
        repo="repo",
        type="new",
        fts_content="core engine logic").to_file_row()
    # File B: 중요도 낮음
    row_b = IndexingResult(
        path=f"{rid}/util.py",
        rel="util.py",
        root_id=rid,
        repo="repo",
        type="new",
        fts_content="core utility helper").to_file_row()

    db.upsert_files_turbo([row_a, row_b])
    db.finalize_turbo_batch()

    # 심볼 중요도 강제 설정
    conn.execute(
        "INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname, importance_score) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("s-eng",
         f"{rid}/engine.py",
         rid,
         "MainEngine",
         "class",
         1,
         10,
         "",
         "MainEngine",
         100.0))
    conn.execute(
        "INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname, importance_score) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("s-util",
         f"{rid}/util.py",
         rid,
         "Helper",
         "func",
         1,
         10,
         "",
         "Helper",
         1.0))

    # 2. 'core' 키워드로 검색 (두 파일 모두 매칭되지만 순위가 갈려야 함)
    opts = SearchOptions(query="core", root_ids=[rid])
    hits, _ = db.search(opts)

    # 3. 검증
    assert len(hits) >= 2
    assert hits[0].path == f"{rid}/engine.py"  # 중요도 100점짜리가 1등이어야 함
    assert "importance=100.0" in hits[0].hit_reason


def test_intelligence_semantic_similarity(db, workspace):
    """벡터 유사도(Semantic)를 통한 의미 기반 검색 검증"""
    rid = workspace
    conn = db.get_read_connection()
    _prepare_db_v2(conn)

    vec = struct.pack("3f", 1.0, 0.0, 0.0)
    conn.execute(
        "INSERT INTO embeddings (root_id, entity_type, entity_id, content_hash, model, vector, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rid,
         "file",
         f"{rid}/auth.py",
         "h1",
         "text-embedding-3",
         vec,
         0,
         0))

    query_vector = [0.9, 0.1, 0.0]
    hits = db.search_repo.search_semantic(
        query_vector, limit=5, root_ids=[rid])

    assert len(hits) > 0
    assert hits[0].path == f"{rid}/auth.py"
    assert hits[0].score > 80.0
