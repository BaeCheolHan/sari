import hashlib
import json
import pytest

from sari.mcp.stabilization.aggregation import reset_bundles_for_tests
from sari.mcp.stabilization.session_state import reset_session_metrics_for_tests
from sari.mcp.tools.read import execute_read


pytestmark = pytest.mark.read


class _DummyDB:
    pass


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _hash12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _wrap(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _assert_success_evidence_contract(payload: dict) -> None:
    if payload.get("error"):
        return
    stabilization = payload.get("meta", {}).get("stabilization", {})
    reasons = stabilization.get("reason_codes") or []
    if "NO_RESULTS" in reasons:
        return
    evidence_refs = stabilization.get("evidence_refs")
    assert isinstance(evidence_refs, list)
    assert len(evidence_refs) > 0


def test_read_file_injects_evidence_refs_with_offset_limit(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_read_file(_args, _db, _roots):
        return _wrap({
            "content": [{"type": "text", "text": "beta\ngamma"}],
            "metadata": {"offset": 1, "limit": 2, "total_lines": 3},
        })

    monkeypatch.setattr("sari.mcp.tools.read.execute_read_file", _fake_read_file)

    target = "repo/a.py"
    result = execute_read(
        {"mode": "file", "target": target, "offset": 1, "limit": 2, "candidate_id": "cand-1"},
        _DummyDB(),
        ["/tmp/ws"],
    )

    evidence = _payload(result)["meta"]["stabilization"]["evidence_refs"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "file"
    assert evidence[0]["path"] == target
    assert evidence[0]["start_line"] == 2
    assert evidence[0]["end_line"] == 3
    assert evidence[0]["candidate_id"] == "cand-1"
    assert evidence[0]["content_hash"] == _hash12("beta\ngamma")


def test_read_symbol_injects_evidence_refs_from_payload_lines(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_read_symbol(_args, _db, _logger, _roots):
        return _wrap({
            "path": "repo/s.py",
            "name": "MySym",
            "start_line": 10,
            "end_line": 12,
            "content": "def x():\n    return 1",
        })

    monkeypatch.setattr("sari.mcp.tools.read.execute_read_symbol", _fake_read_symbol)

    result = execute_read(
        {"mode": "symbol", "target": "MySym", "path": "repo/s.py"},
        _DummyDB(),
        ["/tmp/ws"],
    )

    evidence = _payload(result)["meta"]["stabilization"]["evidence_refs"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "symbol"
    assert evidence[0]["path"] == "repo/s.py"
    assert evidence[0]["start_line"] == 10
    assert evidence[0]["end_line"] == 12
    assert evidence[0]["symbol"] == "MySym"
    assert evidence[0]["content_hash"] == _hash12("def x():\n    return 1")


def test_read_snippet_injects_evidence_refs_per_result(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_get_snippet(_args, _db, _roots):
        return _wrap({
            "tag": "tag-x",
            "results": [
                {
                    "id": 1,
                    "path": "repo/a.py",
                    "start_line": 3,
                    "end_line": 4,
                    "content": "x\ny",
                },
                {
                    "id": 2,
                    "path": "repo/b.py",
                    "start_line": 8,
                    "end_line": 8,
                    "content": "z",
                },
            ],
        })

    monkeypatch.setattr("sari.mcp.tools.read.execute_get_snippet", _fake_get_snippet)

    result = execute_read(
        {"mode": "snippet", "target": "tag-x", "candidate_id": "cand-3"},
        _DummyDB(),
        ["/tmp/ws"],
    )

    evidence = _payload(result)["meta"]["stabilization"]["evidence_refs"]
    assert len(evidence) == 2
    assert evidence[0]["kind"] == "snippet"
    assert evidence[0]["path"] == "repo/a.py"
    assert evidence[0]["start_line"] == 3
    assert evidence[0]["end_line"] == 4
    assert evidence[0]["content_hash"] == _hash12("x\ny")
    assert evidence[1]["kind"] == "snippet"
    assert evidence[1]["path"] == "repo/b.py"
    assert evidence[1]["start_line"] == 8
    assert evidence[1]["end_line"] == 8
    assert evidence[1]["content_hash"] == _hash12("z")


def test_read_diff_preview_injects_diff_evidence(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_diff(_args, _db, _roots):
        return _wrap({
            "path": "repo/c.py",
            "against": "HEAD",
            "diff": "@@ -1 +1 @@\n-a\n+b",
        })

    monkeypatch.setattr("sari.mcp.tools.read.execute_dry_run_diff", _fake_diff)

    result = execute_read(
        {"mode": "diff_preview", "target": "repo/c.py", "content": "b", "against": "HEAD"},
        _DummyDB(),
        ["/tmp/ws"],
    )

    evidence = _payload(result)["meta"]["stabilization"]["evidence_refs"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "diff"
    assert evidence[0]["path"] == "repo/c.py"
    assert evidence[0]["against"] == "HEAD"
    assert evidence[0]["content_hash"] == _hash12("@@ -1 +1 @@\n-a\n+b")


def test_read_file_empty_content_still_emits_evidence_with_empty_range(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_read_file(_args, _db, _roots):
        return _wrap({
            "content": [{"type": "text", "text": ""}],
            "metadata": {"offset": 3, "limit": 2, "total_lines": 3},
        })

    monkeypatch.setattr("sari.mcp.tools.read.execute_read_file", _fake_read_file)

    result = execute_read(
        {"mode": "file", "target": "repo/empty.txt", "offset": 3, "limit": 2, "candidate_id": "cand-empty"},
        _DummyDB(),
        ["/tmp/ws"],
    )
    evidence = _payload(result)["meta"]["stabilization"]["evidence_refs"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "file"
    assert evidence[0]["start_line"] == 4
    assert evidence[0]["end_line"] == 3
    assert evidence[0]["content_hash"] == _hash12("")
    assert evidence[0]["candidate_id"] == "cand-empty"


def test_read_snippet_zero_results_is_no_results_not_success(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    def _fake_get_snippet(_args, _db, _roots):
        return _wrap({"tag": "tag-zero", "results": []})

    monkeypatch.setattr("sari.mcp.tools.read.execute_get_snippet", _fake_get_snippet)

    result = execute_read(
        {"mode": "snippet", "target": "tag-zero"},
        _DummyDB(),
        ["/tmp/ws"],
    )
    payload = _payload(result)
    assert payload["error"]["code"] == "NO_RESULTS"
    stabilization = payload["meta"]["stabilization"]
    assert stabilization["reason_codes"] == ["NO_RESULTS"]
    assert stabilization["evidence_refs"] == []
    assert stabilization["next_calls"]


@pytest.mark.parametrize(
    "mode,read_request,fake_payload",
    [
        ("file", {"mode": "file", "target": "repo/f.py", "offset": 0, "limit": 1}, {"content": [{"type": "text", "text": "x"}]}),
        ("symbol", {"mode": "symbol", "target": "Sym", "path": "repo/s.py"}, {"path": "repo/s.py", "start_line": 1, "end_line": 1, "content": "class Sym: pass", "name": "Sym"}),
        ("snippet", {"mode": "snippet", "target": "tag1"}, {"tag": "tag1", "results": [{"path": "repo/a.py", "start_line": 1, "end_line": 1, "content": "x"}]}),
        ("diff_preview", {"mode": "diff_preview", "target": "repo/d.py", "content": "b", "against": "HEAD"}, {"path": "repo/d.py", "against": "HEAD", "diff": "@@ -1 +1 @@\n-a\n+b"}),
    ],
)
def test_read_success_global_contract_requires_non_empty_evidence_refs(monkeypatch, mode, read_request, fake_payload):
    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")
    reset_session_metrics_for_tests()
    reset_bundles_for_tests()

    if mode == "file":
        monkeypatch.setattr("sari.mcp.tools.read.execute_read_file", lambda *_: _wrap(fake_payload))
    elif mode == "symbol":
        monkeypatch.setattr("sari.mcp.tools.read.execute_read_symbol", lambda *_: _wrap(fake_payload))
    elif mode == "snippet":
        monkeypatch.setattr("sari.mcp.tools.read.execute_get_snippet", lambda *_: _wrap(fake_payload))
    else:
        monkeypatch.setattr("sari.mcp.tools.read.execute_dry_run_diff", lambda *_: _wrap(fake_payload))

    result = execute_read(read_request, _DummyDB(), ["/tmp/ws"])
    payload = _payload(result)
    _assert_success_evidence_contract(payload)
