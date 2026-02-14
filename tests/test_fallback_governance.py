from sari.core.fallback_governance import (
    fallback_taxonomy,
    note_fallback_event,
    reset_fallback_metrics_for_tests,
    snapshot_fallback_metrics,
)


def test_fallback_taxonomy_has_required_ids():
    table = fallback_taxonomy()
    assert "registry_path_fallback" in table
    assert "port_auto_fallback" in table
    assert "search_text_fallback" in table


def test_fallback_metrics_records_enter_exit():
    reset_fallback_metrics_for_tests()
    note_fallback_event(
        "port_auto_fallback",
        trigger="daemon_port_in_use",
        exit_condition="fallback_port_selected",
    )
    snap = snapshot_fallback_metrics()
    rows = {row["fallback_id"]: row for row in snap["rows"]}
    assert rows["port_auto_fallback"]["enter_count"] == 1
    assert rows["port_auto_fallback"]["exit_count"] == 1
    assert rows["port_auto_fallback"]["active"] is False


def test_fallback_metrics_persists_to_file(tmp_path, monkeypatch):
    from sari.core import fallback_governance as fg

    metrics_file = tmp_path / "fallback_metrics.json"
    monkeypatch.setenv("SARI_FALLBACK_METRICS_FILE", str(metrics_file))
    fg.reset_fallback_metrics_for_tests()
    note_fallback_event(
        "search_text_fallback",
        trigger="engine_exception:RuntimeError",
        exit_condition="fallback_result_returned",
    )

    # Simulate process restart: clear in-memory state only, then expect reload from persisted file.
    fg._STATS.clear()  # type: ignore[attr-defined]
    fg._ACTIVE_SINCE.clear()  # type: ignore[attr-defined]
    fg._LOADED = False  # type: ignore[attr-defined]

    rows = {row["fallback_id"]: row for row in snapshot_fallback_metrics()["rows"]}
    assert rows["search_text_fallback"]["enter_count"] >= 1
