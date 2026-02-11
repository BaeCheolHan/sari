import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, *, target: str):
        self.target = target

    def search(self, _opts):
        return [SearchHit(repo="r", path=self.target, score=1.0, snippet="hit")], {"total": 1}

    def read_file(self, _path: str):
        return "a\nb\nc\n"

    def read_symbol(self, _query, _limit):
        return {"path": self.target, "name": "Sym", "line": 1, "content": "class Sym:\n    pass\n"}


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


def test_read_gate_blocks_when_candidate_ref_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    execute_search({"session_id": "g-1", "query": "a", "search_type": "code"}, db, None, [str(tmp_path)])
    blocked = execute_read({"session_id": "g-1", "mode": "file", "target": target}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_REF_REQUIRED"


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


def test_read_gate_blocks_precision_read_over_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    blocked = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 500}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_REF_REQUIRED"


def test_read_gate_allows_precision_read_exact_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    ok = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 200}, db, [str(tmp_path)])
    assert ok.get("isError") is not True


def test_read_gate_blocks_precision_read_over_default_cap_by_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target=target)
    blocked = execute_read({"mode": "file", "target": target, "offset": 0, "limit": 201}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "SEARCH_REF_REQUIRED"


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
