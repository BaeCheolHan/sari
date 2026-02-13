from unittest.mock import MagicMock

from filelock import Timeout

from sari.mcp.cli import daemon_lifecycle_lock as lock_mod


def test_run_with_lifecycle_lock_returns_action_result(monkeypatch):
    fake_lock = MagicMock()
    fake_lock.__enter__.return_value = fake_lock
    fake_lock.__exit__.return_value = False
    monkeypatch.setattr(lock_mod, "FileLock", lambda *args, **kwargs: fake_lock)

    rc = lock_mod.run_with_lifecycle_lock("start", lambda: 3)

    assert rc == 3


def test_run_with_lifecycle_lock_returns_error_on_timeout(monkeypatch, capsys):
    class _TimeoutLock:
        def __enter__(self):
            raise Timeout("busy")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(lock_mod, "FileLock", lambda *args, **kwargs: _TimeoutLock())

    rc = lock_mod.run_with_lifecycle_lock("refresh", lambda: 0)

    assert rc == 1
    assert "lifecycle operation is in progress" in capsys.readouterr().err
