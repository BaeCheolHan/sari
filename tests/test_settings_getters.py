from sari.core.settings import Settings


def test_get_int_returns_default_when_attribute_value_is_invalid():
    s = Settings()
    s.ENGINE_RELOAD_MS = "invalid"
    assert s.get_int("ENGINE_RELOAD_MS", 1234) == 1234


def test_get_int_returns_default_when_env_value_is_invalid(monkeypatch):
    monkeypatch.setenv("SARI_UNKNOWN_INT", "NaN")
    s = Settings()
    assert s.get_int("UNKNOWN_INT", 77) == 77
