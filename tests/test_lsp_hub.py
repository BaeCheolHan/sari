import time

from sari.core.lsp.hub import LSPHub, prewarm_lsp_hub_from_db


def test_lsp_hub_backpressure_returns_error():
    hub = LSPHub()
    lang = "python"
    sem = hub._semaphores.setdefault(lang, __import__("threading").Semaphore(1))
    assert sem.acquire(blocking=False) is True
    try:
        ok, symbols, err = hub.request_document_symbols(source_path="/tmp/a.py", source="def a():\n    pass\n")
        assert ok is False
        assert symbols == []
        assert err == "ERR_BACKPRESSURE"
    finally:
        sem.release()


def test_lsp_hub_unavailable_without_server():
    hub = LSPHub()
    ok, symbols, err = hub.request_document_symbols(source_path="/tmp/a.unknown_ext", source="x")
    assert ok is False
    assert symbols == []
    assert err == "ERR_LSP_UNAVAILABLE"


def test_lsp_hub_metrics_snapshot_shape():
    hub = LSPHub()
    snap = hub.metrics_snapshot()
    for k in (
        "language_cold_start_count",
        "lsp_restart_count",
        "lsp_timeout_rate",
        "lsp_timeout_count",
        "lsp_request_count",
        "lsp_backpressure_count",
        "active_languages",
    ):
        assert k in snap


def test_lsp_hub_open_breaker_blocks_start(monkeypatch):
    hub = LSPHub()
    lang = "python"
    hub._breaker[lang] = {"fail_count": 99, "open_until": time.time() + 10.0}
    cli = hub.get_or_start(lang, "/tmp/a.py")
    assert cli is None


def test_prewarm_lsp_hub_from_db_starts_top_languages(monkeypatch):
    class _Cur:
        @staticmethod
        def fetchall():
            return [
                {"path": "rid/a.py"},
                {"path": "rid/b.py"},
                {"path": "rid/c.ts"},
                {"path": "rid/d.rs"},
            ]

    class _Conn:
        @staticmethod
        def execute(_sql: str):
            return _Cur()

    class _DB:
        @staticmethod
        def get_read_connection():
            return _Conn()

    started = {"n": 0}

    class _FakeHub:
        @staticmethod
        def get_or_start(_lang: str, _path: str):
            started["n"] += 1
            return object()

    monkeypatch.setattr("sari.core.lsp.hub.get_lsp_hub", lambda: _FakeHub())
    n = prewarm_lsp_hub_from_db(_DB(), top_n=2)
    assert n == 2
    assert started["n"] == 2
