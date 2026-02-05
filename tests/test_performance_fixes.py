import pytest
import time
import zlib
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.mcp.server import LocalSearchMCPServer
from sari.core.settings import settings

try:
    import tantivy
    from sari.core.engine.tantivy_engine import TantivyEngine
except ImportError:
    tantivy = None

@pytest.fixture
def tantivy_engine(tmp_path):
    if tantivy is None:
        pytest.skip("tantivy library not installed")
    index_path = tmp_path / "tantivy_index"
    return TantivyEngine(str(index_path))

@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    return LocalSearchDB(str(db_path))

def test_tantivy_writer_reuse_and_commit(tantivy_engine):
    docs = [
        {"root_id": "r1", "doc_id": "r1/file1.py", "repo": "repo1", "body_text": "content one", "mtime": 100, "size": 10},
        {"root_id": "r1", "doc_id": "r1/file2.py", "repo": "repo1", "body_text": "content two", "mtime": 101, "size": 11},
    ]
    # 첫 번째 업서트
    tantivy_engine.upsert_documents(docs)
    writer1 = tantivy_engine._writer
    assert writer1 is not None
    
    # 두 번째 업서트 (writer 재사용 확인)
    tantivy_engine.upsert_documents(docs)
    assert tantivy_engine._writer is writer1
    
    # 검색 결과 확인 (커밋이 정상적으로 되었는지)
    time.sleep(0.1) # 인덱스 반영 대기
    results = tantivy_engine.search("content")
    assert len(results) == 2

def test_tantivy_query_escape(tantivy_engine):
    # 특수문자가 포함된 쿼리가 에러 없이 처리되는지 확인
    unsafe_query = "def hello(): # test (case) [bracket] {brace}"
    try:
        results = tantivy_engine.search(unsafe_query)
        assert isinstance(results, list)
    except Exception as e:
        pytest.fail(f"Tantivy search failed with special characters: {e}")

def test_db_content_compression(db):
    # 압축 저장 및 복원 테스트
    root_id = "test_root"
    db.upsert_root(root_id, "/tmp", "/tmp") # 부모 키 존재 보장
    
    original_content = "import os\n" * 100 
    compressed_data = b"ZLIB\0" + zlib.compress(original_content.encode("utf-8"))
    
    # 직접 TX로 삽입
    cur = db._write.cursor()
    rows = [
        (f"{root_id}/test.py", "test.py", root_id, "repo", 100, len(original_content), 
         compressed_data, "hash", "", int(time.time()), 0, "ok", "none", "none", "none", 0, 0, 0, len(original_content), "{}")
    ]
    db.upsert_files_tx(cur, rows)
    db._write.commit()
    
    restored = db.read_file(f"{root_id}/test.py")
    assert restored == original_content

def test_mcp_server_async_dispatch(tmp_path):
    server = LocalSearchMCPServer(str(tmp_path))
    # 여러 개의 핑 요청을 큐에 넣고 처리 확인
    for i in range(5):
        req = {"method": "ping", "params": {}, "id": i}
        server._req_queue.put(req)
    
    # 잠시 대기 후 큐가 비워졌는지 확인
    timeout = time.time() + 2
    while not server._req_queue.empty() and time.time() < timeout:
        time.sleep(0.1)
    
    assert server._req_queue.empty()