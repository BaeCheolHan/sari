import pytest


def test_app_wrapper_exports_allowlisted_modules_only():
    from sari import app

    expected = {
        "config",
        "db",
        "dedup_queue",
        "engine_registry",
        "engine_runtime",
        "http_server",
        "indexer",
        "main",
        "models",
        "queue_pipeline",
        "ranking",
        "registry",
        "search_engine",
        "watcher",
        "workspace",
    }
    assert expected.issubset(set(app.LEGACY_MODULE_MAP.keys()))


def test_app_wrapper_unknown_module_error_is_actionable():
    from sari import app

    with pytest.raises(ImportError) as exc:
        app.resolve_legacy_module("unknown_module")
    message = str(exc.value)
    assert "Legacy module 'app.unknown_module' is not supported" in message
    assert "Use 'sari.core' imports instead" in message


def test_cli_main_routes_daemon_command_without_legacy_main(monkeypatch):
    from sari.mcp import cli

    called = {"stop": False}

    def _fake_stop(_args):
        called["stop"] = True
        return 0

    monkeypatch.setattr(cli, "cmd_daemon_stop", _fake_stop)
    rc = cli.main(["daemon", "stop", "--all"])
    assert rc == 0
    assert called["stop"] is True
