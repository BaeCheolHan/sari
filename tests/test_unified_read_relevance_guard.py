import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.aggregation import reset_bundles_for_tests
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, text: str, hits: list[SearchHit]):
        self._text = text
        self._hits = hits

    def search(self, _opts):
        return self._hits, {"total": len(self._hits)}

    def read_file(self, _path: str):
        return self._text


def _payload(res: dict) -> dict:
    return json.loads(res["content"][0]["text"])


def test_relevance_guard_warns_for_unrelated_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    top_file = tmp_path / "a.py"
    other_file = tmp_path / "other.py"
    top_file.write_text("x\n", encoding="utf-8")
    other_file.write_text("x\n", encoding="utf-8")
    hits = [SearchHit(repo="r", path=str(top_file), score=1.0, snippet="a")]
    db = StubDB("x\n", hits)
    execute_search({"session_id": "rel-1", "query": "foo", "search_type": "code"}, db, None, [str(tmp_path)])
    res = execute_read(
        {"session_id": "rel-1", "mode": "file", "target": str(other_file), "offset": 0, "limit": 1},
        db,
        [str(tmp_path)],
    )
    stab = _payload(res)["meta"]["stabilization"]
    assert any("unrelated" in w.lower() for w in stab["warnings"])
    assert stab["suggested_next_action"] == "search"
    assert stab["alternatives"]


def test_relevance_guard_no_warning_for_topk_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    top_file = tmp_path / "a.py"
    top_file.write_text("x\n", encoding="utf-8")
    hits = [SearchHit(repo="r", path=str(top_file), score=1.0, snippet="a")]
    db = StubDB("x\n", hits)
    search_res = execute_search({"session_id": "rel-2", "query": "foo", "search_type": "code"}, db, None, [str(tmp_path)])
    candidate_id = _payload(search_res)["matches"][0]["candidate_id"]

    res = execute_read({"session_id": "rel-2", "mode": "file", "target": str(top_file), "candidate_id": candidate_id}, db, [str(tmp_path)])
    stab = _payload(res)["meta"]["stabilization"]
    assert not any("unrelated" in w.lower() for w in stab["warnings"])
