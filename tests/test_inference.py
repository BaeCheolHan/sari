import pytest
from sari.mcp.tools.inference import resolve_search_intent

def test_inference_sql_blocker():
    resolved, reason = resolve_search_intent('SELECT * FROM users')
    assert resolved == 'code'
    assert 'blocked' in reason

def test_inference_api():
    resolved, reason = resolve_search_intent('/api/v1/login')
    assert resolved == 'api'
    resolved, reason = resolve_search_intent('GET /users')
    assert resolved == 'api'

def test_inference_symbol():
    resolved, reason = resolve_search_intent('LoginService')
    assert resolved == 'symbol'
    resolved, reason = resolve_search_intent('auth.login')
    assert resolved == 'symbol'
    resolved, reason = resolve_search_intent('Namespace::Method')
    assert resolved == 'symbol'

def test_inference_code_default():
    resolved, reason = resolve_search_intent('how to login user')
    assert resolved == 'code'