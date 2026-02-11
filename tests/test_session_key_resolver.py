from sari.mcp.stabilization.session_keys import resolve_session_key, workspace_hash


def test_session_key_prefers_session_id_over_connection_id(tmp_path):
    roots = [str(tmp_path)]
    key = resolve_session_key({"session_id": "sid-1", "connection_id": "conn-1"}, roots)
    assert key == f"ws:{workspace_hash(roots)}:sid:sid-1"


def test_session_key_falls_back_to_connection_id(tmp_path):
    roots = [str(tmp_path)]
    key = resolve_session_key({"connection_id": "conn-1"}, roots)
    assert key == f"ws:{workspace_hash(roots)}:conn:conn-1"


def test_session_key_uses_unknown_connection_when_missing(tmp_path):
    roots = [str(tmp_path)]
    key = resolve_session_key({}, roots)
    assert key == f"ws:{workspace_hash(roots)}:conn:unknown"
