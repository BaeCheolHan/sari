import json

from sari.mcp.stabilization.aggregation import reset_bundles_for_tests
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read


class StubDB:
    def __init__(self, text: str):
        self._text = text

    def read_file(self, _path: str):
        return self._text


def _payload(res: dict) -> dict:
    return json.loads(res["content"][0]["text"])


def test_read_budget_soft_limit_reduces_large_limit_and_sets_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("a\n" * 1000, encoding="utf-8")
    db = StubDB("a\n" * 1000)
    target = str(target_file)
    res = execute_read({"session_id": "s-soft", "mode": "file", "target": target, "limit": 1000}, db, [str(tmp_path)])
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
    for _ in range(25):
        res = execute_read({"session_id": "s-hard", "mode": "file", "target": target}, db, [str(tmp_path)])
        assert res.get("isError") is not True

    blocked = execute_read({"session_id": "s-hard", "mode": "file", "target": target}, db, [str(tmp_path)])
    p = _payload(blocked)
    assert p["error"]["code"] == "BUDGET_EXCEEDED"
