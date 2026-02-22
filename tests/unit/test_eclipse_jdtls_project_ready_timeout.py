import importlib
import os


def _mod():
    import solidlsp.language_servers.eclipse_jdtls as m
    return m


def test_jdtls_project_ready_timeout_default(monkeypatch):
    monkeypatch.delenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', raising=False)
    m = _mod()
    assert m._project_ready_timeout_seconds() == 20


def test_jdtls_project_ready_timeout_env_override(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', '3')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 3


def test_jdtls_project_ready_timeout_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', 'oops')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 20


def test_jdtls_project_ready_timeout_negative_clamped(monkeypatch):
    monkeypatch.setenv('SARI_JDTLS_PROJECT_READY_TIMEOUT_SEC', '-1')
    m = _mod()
    assert m._project_ready_timeout_seconds() == 0
