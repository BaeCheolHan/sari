from types import SimpleNamespace

from sari.core.engine_registry import default_engine_name


def test_default_engine_name_tolerates_non_string_mode():
    cfg = SimpleNamespace(engine_mode=123)
    name = default_engine_name(cfg)
    assert name in {"sqlite", "embedded"}
