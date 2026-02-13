from sari.mcp.stabilization import aggregation as ag


def test_bundles_store_is_capped(monkeypatch):
    ag.reset_bundles_for_tests()
    monkeypatch.setattr(ag, "_MAX_BUNDLES", 32, raising=False)

    for i in range(200):
        ag.add_read_to_bundle(
            f"session-{i}",
            mode="file",
            path=f"/tmp/{i}.py",
            text="abc",
        )

    assert len(ag._BUNDLES) <= 32


def test_bundle_items_are_capped(monkeypatch):
    ag.reset_bundles_for_tests()
    monkeypatch.setattr(ag, "_MAX_BUNDLE_ITEMS", 16, raising=False)

    session_key = "session-fixed"
    for i in range(200):
        ag.add_read_to_bundle(
            session_key,
            mode="file",
            path=f"/tmp/{i}.py",
            text=f"content-{i}",
        )

    bundle = ag._BUNDLES[session_key]
    assert len(bundle.items) <= 16
    assert len(bundle.seen) <= 16
