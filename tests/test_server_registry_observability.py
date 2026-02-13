import logging


def test_server_registry_safe_load_warns_on_invalid_json(caplog):
    from sari.core.server_registry import ServerRegistry

    reg = ServerRegistry()
    with caplog.at_level(logging.WARNING, logger="sari.server_registry"):
        data = reg._safe_load("{not-json")

    assert data["version"] == "2.0"
    assert any("Failed to parse registry JSON" in r.message for r in caplog.records)


def test_server_registry_fallback_warning_emits_once(monkeypatch, caplog):
    import sari.core.server_registry as sr

    monkeypatch.setenv("SARI_REGISTRY_FILE", "")
    monkeypatch.setattr(sr, "_ensure_writable_dir", lambda _p: False)
    monkeypatch.setattr(sr, "_FALLBACK_WARNED", False)

    with caplog.at_level(logging.WARNING, logger="sari.server_registry"):
        _ = sr.get_registry_path()
        _ = sr.get_registry_path()

    warns = [r for r in caplog.records if "Default registry path not writable" in r.message]
    assert len(warns) == 1
