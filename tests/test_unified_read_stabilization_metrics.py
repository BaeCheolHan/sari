import json

from sari.core.models import SearchHit
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read
from sari.mcp.tools.search import execute_search


class StubDB:
    def __init__(self, *, file_content: str, hits: list[SearchHit], search_meta: dict | None = None):
        self._file_content = file_content
        self._hits = hits
        self._search_meta = search_meta or {"total": len(hits)}

    def search_v2(self, _opts):
        return self._hits, self._search_meta

    def read_file(self, _path: str):
        return self._file_content


def _json_payload(tool_result: dict) -> dict:
    return json.loads(tool_result["content"][0]["text"])


def test_search_response_includes_stabilization_metrics_snapshot(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    hits = [SearchHit(repo="repo1", path=f"p{i}.py", score=1.0, snippet="X" * 2000) for i in range(10)]
    db = StubDB(file_content="", hits=hits, search_meta={"total": 10})

    result = execute_search({"query": "needle", "search_type": "code", "limit": 10}, db, None, ["/tmp/ws-a"])

    payload = _json_payload(result)
    snapshot = payload["meta"]["stabilization"]["metrics_snapshot"]
    assert snapshot["search_count"] == 1
    assert snapshot["preview_degraded_count"] == 1
    assert snapshot["reads_count"] == 0
    assert snapshot["read_after_search_ratio"] == 0.0


def test_read_response_includes_metrics_and_updates_deterministically(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    file_path = tmp_path / "sample.py"
    file_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

    hits = [SearchHit(repo="repo1", path="x.py", score=1.0, snippet="hit")]
    db = StubDB(file_content="line1\nline2\nline3\n", hits=hits, search_meta={"total": 1})

    execute_search({"query": "line", "search_type": "code", "limit": 1}, db, None, [str(tmp_path)])
    read_result = execute_read({"mode": "file", "target": str(file_path)}, db, [str(tmp_path)])

    payload = _json_payload(read_result)
    snapshot = payload["meta"]["stabilization"]["metrics_snapshot"]
    read_text = payload["content"][0]["text"]
    assert snapshot["reads_count"] == 1
    assert snapshot["reads_lines_total"] == 3
    assert snapshot["reads_chars_total"] == len(read_text)
    assert snapshot["avg_read_span"] == 3.0
    assert snapshot["max_read_span"] == 3
    assert snapshot["search_count"] == 1
    assert snapshot["read_after_search_ratio"] == 1.0


def test_metrics_are_isolated_per_roots_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    file_a = root_a / "a.py"
    file_b = root_b / "b.py"
    file_a.write_text("alpha\nbeta\n", encoding="utf-8")
    file_b.write_text("gamma\n", encoding="utf-8")

    db_a = StubDB(file_content="alpha\nbeta\n", hits=[SearchHit(repo="ra", path="a.py", score=1.0, snippet="alpha")], search_meta={"total": 1})
    db_b = StubDB(file_content="gamma\n", hits=[], search_meta={"total": 0})

    execute_search({"query": "alpha", "search_type": "code", "limit": 1}, db_a, None, [str(root_a)])
    read_a = execute_read({"mode": "file", "target": str(file_a)}, db_a, [str(root_a)])
    read_b = execute_read({"mode": "file", "target": str(file_b)}, db_b, [str(root_b)])

    snapshot_a = _json_payload(read_a)["meta"]["stabilization"]["metrics_snapshot"]
    snapshot_b = _json_payload(read_b)["meta"]["stabilization"]["metrics_snapshot"]

    assert snapshot_a["search_count"] == 1
    assert snapshot_a["reads_count"] == 1
    assert snapshot_b["search_count"] == 0
    assert snapshot_b["reads_count"] == 1
