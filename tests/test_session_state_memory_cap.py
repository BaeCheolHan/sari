from sari.mcp.stabilization import session_state as ss


def test_session_metrics_store_is_capped(monkeypatch):
    ss.reset_session_metrics_for_tests()
    monkeypatch.setattr(ss, "_MAX_SESSION_METRICS", 32, raising=False)

    for i in range(200):
        ss.record_search_metrics(
            {"connection_id": f"conn-{i}"},
            ["/tmp/ws-memory-cap"],
            preview_degraded=False,
            query="q",
        )

    assert len(ss._SESSION_METRICS) <= 32
