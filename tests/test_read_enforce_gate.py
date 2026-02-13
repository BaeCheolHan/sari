import json
import pytest

from sari.core.models import SearchHit
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


pytestmark = pytest.mark.read


class StubDB:
    def __init__(self, *, target: str):
        self.target = target

    def search(self, _opts):
        return [SearchHit(repo="r", path=self.target, score=1.0, snippet="hit")], {"total": 1}

    def read_file(self, _path: str):
        return "a\nb\nc\n"

    def read_symbol(self, _query, _limit):
        return {"path": self.target, "name": "Sym", "line": 1, "content": "class Sym:\n    pass\n"}

    def list_snippets_by_tag(self, _tag: str, limit: int | None = None):
        rows = [
            {
                "id": 1,
                "tag": "tag1",
                "path": self.target,
                "root_id": "r",
                "start_line": 1,
                "end_line": 2,
                "content": "a\nb\n",
            }
        ]
        if isinstance(limit, int) and limit > 0:
            return rows[:limit]
        return rows


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_read_gate_blocks_when_no_search_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    blocked = execute_read({"mode": "file", "target": target}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_FIRST_REQUIRED"
    assert "Run search(query=" in payload["error"]["message"]


def test_read_gate_blocks_when_candidate_ref_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    execute_search({"session_id": "g-1", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    blocked = execute_read({"session_id": "g-1", "mode": "file", "target": target}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_REF_REQUIRED"
    assert "SARI_NEXT" in payload["error"]["message"]


def test_read_gate_allows_candidate_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    search_result = execute_search({"session_id": "g-2", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(search_result)["matches"][0]["candidate_id"]
    ok = execute_read({"session_id": "g-2", "mode": "file", "target": target, "candidate_id": candidate_id}, db, [str(tmp_path)])
    assert ok.get("isError") is not True


def test_read_gate_allows_precision_read_under_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    ok = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 2}, db, [str(tmp_path)])
    assert ok.get("isError") is not True


def test_read_gate_auto_chunks_precision_read_over_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    result = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 500}, db, [str(tmp_path)])
    payload = _payload(result)
    assert "error" not in payload
    assert payload["metadata"]["limit"] == 200
    warnings = payload["meta"]["stabilization"]["warnings"]
    assert any("Auto-chunked read limit to max_range_lines=200" in w for w in warnings)


def test_read_gate_allows_precision_read_exact_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    ok = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 200}, db, [str(tmp_path)])
    assert ok.get("isError") is not True


def test_read_gate_auto_chunks_precision_read_over_default_cap_by_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    result = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 201}, db, [str(tmp_path)])
    payload = _payload(result)
    assert "error" not in payload
    assert payload["metadata"]["limit"] == 200


def test_read_gate_warn_mode_allows_without_ref_and_emits_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    result = execute_read({"mode": "file", "target": target}, db, [str(tmp_path)])
    payload = _payload(result)
    reasons = payload["meta"]["stabilization"]["reason_codes"]
    assert "SEARCH_FIRST_REQUIRED" in reasons


def test_read_strict_session_id_required(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_STRICT_SESSION_ID", "1")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    blocked = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 1}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "STRICT_SESSION_ID_REQUIRED"


def test_read_gate_blocks_candidate_id_target_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    other = str(tmp_path / "b.py")
    db = StubDB(target=target)
    search_result = execute_search({"session_id": "g-3", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(search_result)["matches"][0]["candidate_id"]
    blocked = execute_read({"session_id": "g-3", "mode": "file", "target": other, "candidate_id": candidate_id}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "CANDIDATE_REF_REQUIRED"
    assert "SARI_NEXT" in payload["error"]["message"]


def test_read_gate_blocks_symbol_candidate_when_path_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    wrong_path = str(tmp_path / "b.py")
    db = StubDB(target=target)
    search_result = execute_search({"session_id": "g-4", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(search_result)["matches"][0]["candidate_id"]
    blocked = execute_read(
        {"session_id": "g-4", "mode": "symbol", "target": "Sym", "path": wrong_path, "candidate_id": candidate_id},
        db,
        [str(tmp_path)],
    )
    payload = _payload(blocked)
    assert payload["error"]["code"] == "CANDIDATE_REF_REQUIRED"


def test_read_gate_blocks_snippet_when_no_search_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    blocked = execute_read({"mode": "snippet", "target": "tag1"}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_FIRST_REQUIRED"
    stabilization = payload["meta"]["stabilization"]
    assert stabilization["reason_codes"]
    assert stabilization["next_calls"]


def test_read_gate_allows_snippet_after_search_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    execute_search({"session_id": "g-snippet", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    ok = execute_read({"session_id": "g-snippet", "mode": "snippet", "target": "tag1"}, db, [str(tmp_path)])
    assert ok.get("isError") is not True


def test_read_gate_block_is_deterministic_for_reason_and_next_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    first = _payload(execute_read({"mode": "snippet", "target": "tag1"}, db, [str(tmp_path)]))
    second = _payload(execute_read({"mode": "snippet", "target": "tag1"}, db, [str(tmp_path)]))
    first_stab = first["meta"]["stabilization"]
    second_stab = second["meta"]["stabilization"]
    assert first_stab["reason_codes"] == second_stab["reason_codes"]
    assert first_stab["next_calls"] == second_stab["next_calls"]


def test_read_strict_session_error_includes_reason_and_next_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_STRICT_SESSION_ID", "1")
    reset_session_metrics_for_tests()
    db = StubDB(target=str(tmp_path / "a.py"))
    payload = _payload(execute_read({"mode": "file", "target": "a.py"}, db, [str(tmp_path)]))
    stabilization = payload["meta"]["stabilization"]
    assert payload["error"]["code"] == "STRICT_SESSION_ID_REQUIRED"
    assert stabilization["reason_codes"] == ["STRICT_SESSION_ID_REQUIRED"]
    assert stabilization["next_calls"]
