from sari.mcp.tools.search_dispatch import dispatch_search


def test_dispatch_uses_symbol_resolve_layer(monkeypatch):
    called = {"symbol": 0, "candidate": 0}

    def _symbol_resolve(_args, *, db, logger, roots, symbol_executor):
        called["symbol"] += 1
        return {"results": [{"name": "A"}], "meta": {}}

    def _candidate(_args, _db, _roots):
        called["candidate"] += 1
        return {"results": [], "meta": {}}

    monkeypatch.setattr("sari.mcp.tools.search_dispatch.execute_symbol_resolve", _symbol_resolve)
    monkeypatch.setattr("sari.mcp.tools.search_dispatch.execute_candidate_search_raw", _candidate)

    raw, resolved, *_ = dispatch_search(
        {"query": "A", "search_type": "symbol", "repo": "r1"},
        db=object(),
        logger=None,
        roots=["/tmp/ws"],
    )
    assert resolved == "symbol"
    assert isinstance(raw, dict)
    assert called["symbol"] == 1
    assert called["candidate"] == 0


def test_dispatch_auto_symbol_fallbacks_to_candidate(monkeypatch):
    called = {"symbol": 0, "candidate": 0}

    def _resolve_search_intent(_query: str):
        return "symbol", None

    def _symbol_resolve(_args, *, db, logger, roots, symbol_executor):
        called["symbol"] += 1
        return {"results": [], "meta": {}}

    def _candidate(_args, _db, _roots):
        called["candidate"] += 1
        return {"results": [{"path": "rid/a.py"}], "meta": {"ok": True}}

    monkeypatch.setattr("sari.mcp.tools.search_dispatch.resolve_search_intent", _resolve_search_intent)
    monkeypatch.setattr("sari.mcp.tools.search_dispatch.execute_symbol_resolve", _symbol_resolve)
    monkeypatch.setattr("sari.mcp.tools.search_dispatch.execute_candidate_search_raw", _candidate)

    raw, resolved, _reason, fallback, _limit = dispatch_search(
        {"query": "A", "search_type": "auto", "repo": "r1"},
        db=object(),
        logger=None,
        roots=["/tmp/ws"],
    )
    assert resolved == "code"
    assert fallback is True
    assert called["symbol"] == 1
    assert called["candidate"] == 1
    assert raw["results"]

