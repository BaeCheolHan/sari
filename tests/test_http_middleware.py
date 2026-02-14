from sari.core.http_middleware import run_http_middlewares


def test_run_http_middlewares_returns_error_on_non_object_result():
    res = run_http_middlewares({}, [], lambda: "bad-result")
    assert res.get("ok") is False
    assert "error" in res
    assert res.get("reason_code") == "HTTP_MIDDLEWARE_EXEC_FAILED"


def test_run_http_middlewares_sanitizes_multiline_error_message():
    res = run_http_middlewares({}, [], lambda: (_ for _ in ()).throw(RuntimeError("boom\nnext")))
    assert res.get("ok") is False
    assert res.get("error") == "boom next"
