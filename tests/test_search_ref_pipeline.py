import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, target: str):
        self.target = target

    def search(self, _opts):
        return [SearchHit(repo="r", path=self.target, score=1.0, snippet="snippet")], {"total": 1}

    def read_file(self, _path: str):
        return "hello\n"


class EmptyDB(StubDB):
    def search(self, _opts):
        return [], {"total": 0}


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_search_emits_candidate_bundle_and_next_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    target = str(tmp_path / "x.py")
    db = StubDB(target)
    result = execute_search({"session_id": "p-1", "query": "x", "search_type": "code"}, db, None, [str(tmp_path)])
    payload = _payload(result)

    assert payload["matches"][0]["candidate_id"]
    stabilization = payload["meta"]["stabilization"]
    assert stabilization["bundle_id"]
    assert stabilization["next_calls"]

    read_args = stabilization["next_calls"][0]["arguments"]
    read_result = execute_read(dict(read_args, session_id="p-1"), db, [str(tmp_path)])
    assert read_result.get("isError") is not True


def test_candidate_ref_is_isolated_by_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    target = str(tmp_path / "x.py")
    db = StubDB(target)
    result = execute_search({"session_id": "iso-a", "query": "x", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(result)["matches"][0]["candidate_id"]
    blocked = execute_read({"session_id": "iso-b", "mode": "file", "target": target, "candidate_id": candidate_id}, db, [str(tmp_path)])
    payload = _payload(blocked)
    assert payload["error"]["code"] == "CANDIDATE_REF_REQUIRED"


def test_search_zero_results_has_search_next_action(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    target = str(tmp_path / "x.py")
    db = EmptyDB(target)
    result = execute_search({"session_id": "zero-1", "query": "none", "search_type": "code"}, db, None, [str(tmp_path)])
    payload = _payload(result)
    stabilization = payload["meta"]["stabilization"]
    assert stabilization["suggested_next_action"] == "search"
    assert stabilization["next_calls"]
    first = stabilization["next_calls"][0]
    assert first["tool"] == "search"
    assert isinstance(first["arguments"], dict)
    assert str(first["arguments"].get("query", "")).strip()


def test_search_deterministic_reason_and_next_calls_for_same_input(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    target = str(tmp_path / "x.py")
    db = StubDB(target)
    a = _payload(execute_search({"session_id": "det-1", "query": "x", "search_type": "code"}, db, None, [str(tmp_path)]))
    b = _payload(execute_search({"session_id": "det-1", "query": "x", "search_type": "code"}, db, None, [str(tmp_path)]))
    a_stab = a["meta"]["stabilization"]
    b_stab = b["meta"]["stabilization"]
    assert a_stab["reason_codes"] == b_stab["reason_codes"]
    assert a_stab["next_calls"] == b_stab["next_calls"]


def test_search_invalid_args_include_reason_and_next_calls(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    result = execute_search([], db=StubDB("/tmp/x.py"), logger=None, roots=["/tmp"])
    payload = _payload(result)
    assert payload["error"]["code"] == "INVALID_ARGS"
    stabilization = payload["meta"]["stabilization"]
    assert stabilization["reason_codes"]
    assert stabilization["next_calls"]
