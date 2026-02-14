import json

from sari.mcp.tools._util import internal_error_response, parse_int_arg, sanitize_error_message


def test_parse_int_arg_accepts_float_like_integer_string():
    value, err = parse_int_arg({"limit": "10.0"}, "limit", 5, "tool", min_value=1)
    assert err is None
    assert value == 10


def test_parse_int_arg_rejects_fractional_string():
    value, err = parse_int_arg({"limit": "10.5"}, "limit", 5, "tool", min_value=1)
    assert value is None
    assert err is not None


def test_sanitize_error_message_compacts_whitespace_and_uses_fallback():
    assert sanitize_error_message(RuntimeError(" boom \n  fail ")) == "boom fail"
    assert sanitize_error_message(RuntimeError(""), "fallback-msg") == "fallback-msg"


def test_internal_error_response_pack_includes_reason_code(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "pack")
    resp = internal_error_response(
        "demo_tool",
        RuntimeError("boom"),
        reason_code="DEMO_FAILED",
        data={"path": "a.py"},
    )
    text = resp["content"][0]["text"]
    assert "PACK1 tool=demo_tool ok=false" in text
    assert "code=INTERNAL" in text
    assert "reason_code=DEMO_FAILED" in text


def test_internal_error_response_json_includes_data_reason_code(monkeypatch):
    monkeypatch.setenv("SARI_FORMAT", "json")
    resp = internal_error_response(
        "demo_tool",
        RuntimeError("boom"),
        reason_code="DEMO_FAILED",
        data={"path": "a.py"},
    )
    assert resp["error"]["code"] == "INTERNAL"
    assert resp["error"]["data"]["reason_code"] == "DEMO_FAILED"
    payload = json.loads(resp["content"][0]["text"])
    assert payload["error"]["data"]["path"] == "a.py"
