import pytest
import os
from sari.mcp.tools.search import execute_search

class MockSymbols:
    def __init__(self, mode):
        self.mode = mode
    def search_symbols(self, query, limit=20, **kwargs):
        if self.mode == 'hits':
            from sari.core.models import SymbolDTO
            return [SymbolDTO(name='LoginService', kind='class', path='auth.py', line=10, symbol_id='id1', qualname='auth.LoginService')]
        return []

class MockDB:
    def __init__(self, mode='empty'):
        self.mode = mode
        self.engine = None
        self.symbols = MockSymbols(mode)
    def search_v2(self, opts):
        if self.mode == 'hits':
            from sari.core.models import SearchHit
            return [SearchHit(path='test.py', repo='root', score=1.0, snippet='test content', hit_reason='test')], {'total': 1}
        return [], {'total': 0}
    def repo_candidates(self, q, limit, root_ids=None):
        return [{'repo': 'test-repo', 'score': 10}]

@pytest.fixture
def db_hits(): return MockDB(mode='hits')

@pytest.fixture
def db_empty(): return MockDB(mode='empty')

@pytest.fixture
def roots(): return ['/mock/root']

def test_search_auto_to_symbol_routing(db_hits, roots):
    os.environ['SARI_FORMAT'] = 'json'
    args = {'query': 'LoginService', 'search_type': 'auto'}
    result = execute_search(args, db_hits, None, roots)
    # v3 정규화된 필드 확인
    assert 'matches' in result
    assert len(result['matches']) > 0
    assert result['matches'][0]['type'] == 'symbol'

def test_search_auto_waterfall_to_code(db_empty, roots):
    os.environ['SARI_FORMAT'] = 'json'
    args = {'query': 'MissingService', 'search_type': 'auto'}
    result = execute_search(args, db_empty, None, roots)
    assert 'matches' in result
    assert result['mode'] == 'code'
    assert result['meta']['fallback_used'] is True
