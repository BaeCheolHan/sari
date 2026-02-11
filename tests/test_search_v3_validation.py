import pytest
import os
from sari.mcp.tools.search import execute_search
from sari.mcp.tools._util import ErrorCode

class MockDB:
    def search(self, opts):
        return [], {'total': 0}

@pytest.fixture
def db():
    return MockDB()

@pytest.fixture
def roots():
    return ['/mock/root']

def test_search_v3_accepts_new_params(db, roots):
    # 환경변수를 JSON 포맷으로 고정하여 검증 용이하게 함
    os.environ['SARI_FORMAT'] = 'json'
    args = {
        'query': 'test',
        'search_type': 'code',
        'preview_mode': 'snippet',
        'context_lines': 5
    }
    result = execute_search(args, db, None, roots)
    assert result.get('isError') is not True

def test_search_v3_invalid_mode_params(db, roots):
    os.environ['SARI_FORMAT'] = 'json'
    # symbol 전용 파라미터를 code 모드에서 사용 시 에러 발생 확인
    args = {
        'query': 'test',
        'search_type': 'code',
        'kinds': ['function']
    }
    result = execute_search(args, db, None, roots)
    assert result.get('isError') is True
    # json 모드에서는 error 필드가 포함됨
    assert 'error' in result
    assert result['error']['code'] == ErrorCode.INVALID_ARGS.value
    assert 'kinds' in result['error']['message']


def test_search_rejects_non_object_args(roots):
    os.environ["SARI_FORMAT"] = "json"
    db = MockDB()
    result = execute_search(["bad-args"], db, None, roots)
    assert result.get("isError") is True
    assert result["error"]["code"] == ErrorCode.INVALID_ARGS.value
