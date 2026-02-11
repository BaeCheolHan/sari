import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, target: str):
        self.target = target

    def search(self, _opts):
        hits = [SearchHit(repo="r", path=self.target, score=1.0, snippet="X" * 2000)]
        hits.extend(SearchHit(repo="r", path=f"{self.target}-{i}", score=1.0, snippet="X" * 2000) for i in range(9))
        return hits, {"total": len(hits)}

    def read_file(self, _path: str):
        return "a\n" * 1000


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_reason_code_enum_is_stable():
    assert [c.value for c in ReasonCode] == [
        "SEARCH_FIRST_REQUIRED",
        "SEARCH_REF_REQUIRED",
        "CANDIDATE_REF_REQUIRED",
        "BUDGET_SOFT_LIMIT",
        "BUDGET_HARD_LIMIT",
        "LOW_RELEVANCE_OUTSIDE_TOPK",
        "PREVIEW_DEGRADED",
    ]


def test_search_emits_preview_degraded_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target)
    result = execute_search({"query": "x", "search_type": "code", "limit": 10}, db, None, [str(tmp_path)])
    reasons = _payload(result)["meta"]["stabilization"]["reason_codes"]
    assert ReasonCode.PREVIEW_DEGRADED.value in reasons


def test_read_emits_budget_soft_limit_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    target = str(tmp_path / "a.py")
    db = StubDB(target)
    search_result = execute_search({"session_id": "rc-1", "query": "x", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(search_result)["matches"][0]["candidate_id"]
    read_result = execute_read(
        {"session_id": "rc-1", "mode": "file", "target": target, "limit": 1000, "candidate_id": candidate_id},
        db,
        [str(tmp_path)],
    )
    reasons = _payload(read_result)["meta"]["stabilization"]["reason_codes"]
    assert ReasonCode.BUDGET_SOFT_LIMIT.value in reasons
