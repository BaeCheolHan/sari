import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.aggregation import reset_bundles_for_tests
from sari.mcp.stabilization.session_state import get_metrics_snapshot, reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, text: str):
        self._text = text

    def search(self, _opts):
        return [SearchHit(repo="r", path=self._path_hint, score=1.0, snippet="hit")], {"total": 1}

    def read_file(self, _path: str):
        return self._text

    def list_snippets_by_tag(self, _tag: str, limit: int | None = None):
        rows = [
            {
                "id": i + 1,
                "tag": "tag1",
                "path": self._path_hint,
                "root_id": "r",
                "start_line": 1,
                "end_line": 4,
                "content": "line1\nline2\nline3\nline4\n",
            }
            for i in range(100)
        ]
        if isinstance(limit, int) and limit > 0:
            return rows[:limit]
        return rows

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
    stabilization = p["meta"]["stabilization"]
    assert stabilization["reason_codes"] == ["BUDGET_HARD_LIMIT"]
    assert stabilization["next_calls"]


def test_read_snippet_soft_limit_caps_max_results_and_context_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("a\n" * 20, encoding="utf-8")
    target = str(target_file)
    db = StubDB("a\n" * 20)
    _candidate_id_for("s-snippet-cap", target, db, str(tmp_path))
    res = execute_read(
        {
            "session_id": "s-snippet-cap",
            "mode": "snippet",
            "target": "tag1",
            "max_results": 999,
            "context_lines": 999,
        },
        db,
        [str(tmp_path)],
    )
    payload = _payload(res)
    stab = payload["meta"]["stabilization"]
    assert stab["budget_state"] == "SOFT_LIMIT"
    assert len(payload["results"]) <= 20
    assert any("snippet results" in w.lower() for w in stab["warnings"])
    assert any("context_lines" in w.lower() for w in stab["warnings"])


def test_read_snippet_increments_line_and_char_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("alpha\nbeta\n", encoding="utf-8")
    target = str(target_file)
    db = StubDB("alpha\nbeta\n")
    _candidate_id_for("s-snippet-metrics", target, db, str(tmp_path))
    result = execute_read(
        {
            "session_id": "s-snippet-metrics",
            "mode": "snippet",
            "target": "tag1",
            "max_preview_chars": 120,
        },
        db,
        [str(tmp_path)],
    )
    payload = _payload(result)
    returned_chars = sum(len(str(r.get("content", ""))) for r in payload.get("results", []))
    assert returned_chars <= 120
    metrics = get_metrics_snapshot({"session_id": "s-snippet-metrics"}, [str(tmp_path)])
    assert int(metrics["reads_lines_total"]) > 0
    assert int(metrics["reads_chars_total"]) > 0
    assert int(metrics["reads_chars_total"]) <= 120


def test_read_diff_preview_respects_max_preview_chars_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("line\n", encoding="utf-8")
    target = str(target_file)
    db = StubDB("line\n")
    candidate_id = _candidate_id_for("s-diff-cap", target, db, str(tmp_path))
    huge = "\n".join(f"line-{i}" for i in range(5000))
    res = execute_read(
        {
            "session_id": "s-diff-cap",
            "mode": "diff_preview",
            "target": target,
            "candidate_id": candidate_id,
            "content": huge,
            "max_preview_chars": 999999,
        },
        db,
        [str(tmp_path)],
    )
    payload = _payload(res)
    stab = payload["meta"]["stabilization"]
    assert stab["budget_state"] == "SOFT_LIMIT"
    assert len(payload["diff"]) <= 12000
