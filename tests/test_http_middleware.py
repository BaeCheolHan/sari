from sari.core.http_middleware import run_http_middlewares


def test_run_http_middlewares_returns_error_on_non_object_result():
    res = run_http_middlewares({}, [], lambda: "bad-result")
    assert res.get("ok") is False
    assert "error" in res
