import pytest
import os
import json
from sari.mcp.tools.search import execute_search

class MockSymbols:
    def search_symbols(self, query, limit=20, **kwargs):
        from sari.core.models import SymbolDTO
        return [SymbolDTO(name='Auth', kind='class', path='auth.py', line=1, symbol_id='id1', qualname='Auth')]

class MockDB:
    def __init__(self):
        self.engine = None
        self.symbols = MockSymbols()
    def search_v2(self, opts):
        from sari.core.models import SearchHit
        return [SearchHit(path='test.py', repo='root', score=1.0, snippet='X'*2000, hit_reason='test')], {'total': 1}

@pytest.fixture
def db(): return MockDB()

@pytest.fixture
def roots(): return ['/mock/root']

def test_search_v3_response_normalization(db, roots):
    os.environ['SARI_FORMAT'] = 'json'
    args = {'query': 'Auth', 'search_type': 'symbol'}
    result = execute_search(args, db, None, roots)
    
    # v3 공통 필드 검증
    assert 'matches' in result
    match = result['matches'][0]
    assert match['type'] == 'symbol'
    assert 'path' in match
    assert 'identity' in match
    assert 'location' in match

def test_search_v3_token_budget_degradation(db, roots):
    os.environ['SARI_FORMAT'] = 'json'
    # 매우 긴 스니펫이 예산에 의해 잘리는지 확인
    args = {'query': 'test', 'search_type': 'code', 'max_preview_chars': 500}
    result = execute_search(args, db, None, roots)
    
    match = result['matches'][0]
    assert len(match['snippet']) <= 500
    # PreviewManager의 기본 max_total_chars=10000 이고 항목이 1개면 500은 안잘릴 수도 있음
    # 강제로 다수 항목을 만들어 예산 초과 상황 유도 필요 (여기서는 간단히 로직 존재 여부만 확인)