from sari.core.models import SearchOptions
from sari.core.search_engine import SearchEngine
from sari.core.db.main import LocalSearchDB

def test_search_ranking_relevance(db: LocalSearchDB):
    """검색 랭킹이 검색어 일치도에 따라 올바르게 정렬되는지 검증"""
    # 1. 데이터 준비
    rid = "root-1"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    db.upsert_files_tx(cur, [
        (f"{rid}/exact.py", "exact.py", rid, "repo", 0, 100, "def find_me(): pass", "h1", "def find_me(): pass", 0, 0, "ok", "", "ok", "", 0, 0, 0, 100, "{}"),
        (f"{rid}/partial.py", "partial.py", rid, "repo", 0, 100, "something else", "h2", "something else", 0, 0, "ok", "", "ok", "", 0, 0, 0, 100, "{}")
    ])
    db._write.commit()
    
    engine = SearchEngine(db)
    
    # 2. 검색 실행
    opts = SearchOptions(query="find_me", limit=10)
    hits, meta = engine.search(opts)
    
    # 3. 결과 검증
    assert len(hits) > 0
    assert hits[0].path == f"{rid}/exact.py"
    assert hits[0].score > 0

def test_search_file_type_filtering(db: LocalSearchDB):
    """확장자 필터링이 정확하게 동작하는지 검증"""
    rid = "root-1"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    db.upsert_files_tx(cur, [
        (f"{rid}/app.py", "app.py", rid, "repo", 0, 10, "print(1)", "h1", "print(1)", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}"),
        (f"{rid}/styles.css", "styles.css", rid, "repo", 0, 10, "body {}", "h2", "body {}", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")
    ])
    db._write.commit()
    
    engine = SearchEngine(db)
    
    # Python 파일만 검색
    opts = SearchOptions(query="1", file_types=["py"])
    hits, _ = engine.search(opts)
    assert len(hits) == 1
    assert hits[0].path.endswith(".py")

    # CSS 파일만 검색
    opts = SearchOptions(query="body", file_types=["css"])
    hits, _ = engine.search(opts)
    assert len(hits) == 1
    assert hits[0].path.endswith(".css")

def test_search_path_pattern_matching(db: LocalSearchDB):
    """경로 패턴(Glob) 매칭이 검색 결과에 올바르게 반영되는지 검증"""
    rid = "root-1"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    cur = db._write.cursor()
    db.upsert_files_tx(cur, [
        (f"{rid}/src/logic.py", "src/logic.py", rid, "repo", 0, 10, "code", "h1", "code", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}"),
        (f"{rid}/tests/test_logic.py", "tests/test_logic.py", rid, "repo", 0, 10, "code", "h2", "code", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}")
    ])
    db._write.commit()
    
    engine = SearchEngine(db)
    
    # 'src' 폴더 내부만 검색
    opts = SearchOptions(query="code", path_pattern="src/**")
    hits, _ = engine.search(opts)
    assert len(hits) == 1
    assert "src/" in hits[0].path
