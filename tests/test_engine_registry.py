from types import SimpleNamespace

from sari.core.engine_registry import default_engine_name
from sari.core.fallback_governance import reset_fallback_metrics_for_tests, snapshot_fallback_metrics


def test_default_engine_name_tolerates_non_string_mode():
    reset_fallback_metrics_for_tests()
    cfg = SimpleNamespace(engine_mode=123)
    name = default_engine_name(cfg)
    assert name in {"sqlite", "embedded"}
    if name == "sqlite":
        rows = {row["fallback_id"]: row for row in snapshot_fallback_metrics()["rows"]}
        assert rows["engine_default_sqlite_fallback"]["enter_count"] >= 1
