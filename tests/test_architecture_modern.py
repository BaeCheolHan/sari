from sari.core.db.main import LocalSearchDB
from sari.core.models import IndexingResult
from sari.core.repository.symbol_repository import SymbolRepository
from sari.core.repository.failed_task_repository import FailedTaskRepository

def test_architecture_facade_properties(db: LocalSearchDB):
    """Facade 프로퍼티를 통한 리포지토리 접근성 검증"""
    # 1. 프로퍼티 존재 확인
    assert isinstance(db.symbols, SymbolRepository)
    assert isinstance(db.tasks, FailedTaskRepository)
    
    # 2. 실제 작동 확인
    db.upsert_root("r1", "/tmp", "/tmp")
    # db.symbols를 통해 직접 쿼리 수행 가능 확인
    symbols = db.symbols.list_symbols_by_path("non-existent")
    assert isinstance(symbols, list)

def test_architecture_dto_first_flow(db: LocalSearchDB):
    """DTO(IndexingResult)를 직접 처리하는 데이터 흐름 검증"""
    rid = "/tmp/ws"
    db.upsert_root(rid, rid, rid)
    
    # 튜플로 변환하지 않고 IndexingResult 객체 리스트를 직접 전달
    results = [
        IndexingResult(path=f"{rid}/app.py", rel="app.py", root_id=rid, repo="repo", type="new"),
        IndexingResult(path=f"{rid}/util.py", rel="util.py", root_id=rid, repo="repo", type="new")
    ]
    
    # 리팩토링된 메서드는 이제 객체를 직접 받을 수 있어야 함
    db.upsert_files_turbo(results)
    db.finalize_turbo_batch()
    
    # 데이터가 정상적으로 저장되었는지 확인
    conn = db.get_read_connection()
    count = conn.execute("SELECT COUNT(1) FROM files WHERE root_id = ?", (rid,)).fetchone()[0]
    assert count == 2
