from sari.mcp.middleware import run_middlewares


def test_run_middlewares_returns_error_on_non_object_result():
    res = run_middlewares("tool", None, {}, [], lambda: "bad-result")
    assert res.get("isError") is True
    assert res["error"]["code"] == -32000
