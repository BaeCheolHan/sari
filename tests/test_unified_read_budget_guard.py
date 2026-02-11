import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.aggregation import reset_bundles_for_tests
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, text: str):
        self._text = text

    def search(self, _opts):
        return [SearchHit(repo="r", path=self._path_hint, score=1.0, snippet="hit")], {"total": 1}

    def read_file(self, _path: str):
        return self._text

    _path_hint = ""


def _payload(res: dict) -> dict:
    return json.loads(res["content"][0]["text"])


def _candidate_id_for(session_id: str, target: str, db: StubDB, root: str) -> str:
    db._path_hint = target
    s = execute_search({"session_id": session_id, "query": "a", "search_type": "code"}, db, None, [root])
    payload = _payload(s)
    return str(payload["matches"][0]["candidate_id"])


def test_read_budget_soft_limit_reduces_large_limit_and_sets_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("a\n" * 1000, encoding="utf-8")
    db = StubDB("a\n" * 1000)
    target = str(target_file)
    candidate_id = _candidate_id_for("s-soft", target, db, str(tmp_path))
    res = execute_read(
        {"session_id": "s-soft", "mode": "file", "target": target, "limit": 1000, "candidate_id": candidate_id},
        db,
        [str(tmp_path)],
    )
    payload = _payload(res)
    stab = payload["meta"]["stabilization"]
    assert stab["budget_state"] == "SOFT_LIMIT"
    assert any("limit" in w.lower() for w in stab["warnings"])


def test_read_budget_hard_limit_returns_budget_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("line\n", encoding="utf-8")
    db = StubDB("line\n")
    target = str(target_file)
    candidate_id = _candidate_id_for("s-hard", target, db, str(tmp_path))
    for _ in range(25):
        res = execute_read({"session_id": "s-hard", "mode": "file", "target": target, "candidate_id": candidate_id}, db, [str(tmp_path)])
        assert res.get("isError") is not True

    blocked = execute_read({"session_id": "s-hard", "mode": "file", "target": target, "candidate_id": candidate_id}, db, [str(tmp_path)])
    p = _payload(blocked)
    assert p["error"]["code"] == "BUDGET_EXCEEDED"
