import pytest
import time
from pathlib import Path
from sari.core.parsers.python import PythonParser
from sari.core.engine.tantivy_engine import TantivyEngine

try:
    import tantivy
except ImportError:
    tantivy = None

def test_python_parser_extraction():
    parser = PythonParser()
    code = """
@decorator
class MyClass:
    '''Class docstring'''
    def method_one(self, x: int) -> str:
        '''Method docstring'''
        return str(x)

def top_level_func():
    return 42
"""
    symbols, relations = parser.extract("test.py", code)
    
    # 클래스 추출 확인
    classes = [s for s in symbols if s[2] == "class"]
    assert len(classes) == 1
    assert classes[0][1] == "MyClass"
    assert "Class docstring" in classes[0][8]
    
    # 메서드 추출 확인
    methods = [s for s in symbols if s[2] == "method"]
    assert len(methods) == 1
    assert methods[0][1] == "method_one"
    assert methods[0][6] == "MyClass" # parent_name
    
    # 함수 추출 확인
    funcs = [s for s in symbols if s[2] == "function"]
    assert len(funcs) == 1
    assert funcs[0][1] == "top_level_func"

def test_tantivy_error_handling(tmp_path):
    if tantivy is None:
        pytest.skip("tantivy not installed")
        
    index_path = tmp_path / "error_idx"
    engine = TantivyEngine(str(index_path))
    
    # 1. 빈 쿼리 처리
    results = engine.search("")
    assert results == []
    
    # 2. 잘못된 구문의 쿼리 (이스케이프가 잘 되는지 확인)
    # 이미 _escape_query가 적용되어 있으므로 에러 없이 빈 결과가 나와야 함
    results = engine.search("body:(unclosed bracket")
    assert isinstance(results, list)

def test_tantivy_lifecycle(tmp_path):
    if tantivy is None:
        pytest.skip("tantivy not installed")
        
    index_path = tmp_path / "lifecycle_idx"
    engine = TantivyEngine(str(index_path))
    
    docs = [{"root_id": "r1", "doc_id": "f1", "repo": "p", "body_text": "hello", "mtime": 0, "size": 0}]
    engine.upsert_documents(docs)
    
    # writer가 열려 있는지 확인
    assert engine._writer is not None
    
    # 수동 클린업 시뮬레이션 (아직 close()가 없으므로 속성 제거)
    # 실제로는 engine.close() 같은 로직이 필요함
    if hasattr(engine, 'close'):
        engine.close()
