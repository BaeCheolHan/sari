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


def test_aggregation_dedupes_identical_reads_deterministically(tmp_path, monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    target_file = tmp_path / "a.py"
    target_file.write_text("same\ncontent\n", encoding="utf-8")
    db = StubDB("same\ncontent\n")
    target = str(target_file)
    r1 = execute_read({"session_id": "agg-1", "mode": "file", "target": target, "offset": 0, "limit": 2}, db, [str(tmp_path)])
    r2 = execute_read({"session_id": "agg-1", "mode": "file", "target": target, "offset": 0, "limit": 2}, db, [str(tmp_path)])

    s1 = _payload(r1)["meta"]["stabilization"]
    s2 = _payload(r2)["meta"]["stabilization"]
    assert s1["context_bundle_id"] == s2["context_bundle_id"]
    assert s2["bundle_items"] == 1
