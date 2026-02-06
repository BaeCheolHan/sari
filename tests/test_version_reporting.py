from sari.core.settings import settings
from sari.version import __version__


def test_settings_version_defaults_to_package_version(monkeypatch):
    monkeypatch.delenv("SARI_VERSION", raising=False)
    assert settings.VERSION == __version__
