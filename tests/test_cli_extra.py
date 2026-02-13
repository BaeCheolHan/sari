import argparse
import io
import json
import sys
import urllib.error
from unittest.mock import MagicMock, patch

from sari.mcp.cli import (
    cmd_doctor,
    cmd_search,
    cmd_status,
    cmd_daemon_stop,
    cmd_daemon_refresh,
)

# Mock psutil
sys.modules['psutil'] = MagicMock()

def test_cmd_doctor():
    args = argparse.Namespace(
        auto_fix=True, auto_fix_rescan=False, 
        no_network=False, no_db=False, no_port=False, no_disk=False,
        include_network=True, include_db=True, include_port=True, include_disk=True,
        min_disk_gb=1.0
    )
    with patch('sari.mcp.tools.doctor.execute_doctor') as mock_exec:
        mock_exec.return_value = {"content": [{"text": "{}"}]}
        ret = cmd_doctor(args)
        assert ret == 0

def test_cmd_search():
    args = argparse.Namespace(query="test", limit=10, repo=None)
    with patch(
        "sari.mcp.cli.commands.status_commands._prepare_http_service",
        return_value=("127.0.0.1", 47777, None),
    ):
        with patch("sari.mcp.cli.commands.status_commands._request_http", return_value={"results": []}):
            ret = cmd_search(args)
            assert ret == 0


def test_cmd_search_handles_http_error_gracefully(capsys):
    args = argparse.Namespace(query="test", limit=10, repo=None)
    with patch(
        "sari.mcp.cli.commands.status_commands._request_http",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        ret = cmd_search(args)
        assert ret == 1
        assert "Error" in capsys.readouterr().out


def test_cmd_search_starts_daemon_when_http_is_down():
    args = argparse.Namespace(
        query="hello",
        limit=5,
        repo=None,
        daemon_host=None,
        daemon_port=None,
        http_host=None,
        http_port=None,
    )
    with patch("sari.mcp.cli.commands.status_commands.get_daemon_address", return_value=("127.0.0.1", 47879)):
        with patch("sari.mcp.cli.commands.status_commands.is_daemon_running", return_value=False):
            with patch("sari.mcp.cli.commands.status_commands._resolve_http_endpoint_for_daemon", return_value=("127.0.0.1", 47777)):
                with patch("sari.mcp.cli.commands.status_commands._is_http_running", side_effect=[False, True]):
                    with patch(
                        "sari.mcp.cli.commands.status_commands._ensure_daemon_running",
                        return_value=("127.0.0.1", 47879, True),
                    ) as mock_ensure:
                        with patch("sari.mcp.cli.commands.status_commands._ensure_workspace_http") as mock_ws:
                            with patch(
                                "sari.mcp.cli.commands.status_commands._request_http",
                                return_value={"ok": True, "results": []},
                            ) as mock_req:
                                ret = cmd_search(args)
                                assert ret == 0
                                mock_ensure.assert_called_once()
                                mock_ws.assert_called()
                                mock_req.assert_called_once_with("/search", {"q": "hello", "limit": 5}, "127.0.0.1", 47777)


def test_cmd_search_uses_shared_preparation_helper():
    args = argparse.Namespace(query="hello", limit=3, repo=None)
    with patch(
        "sari.mcp.cli.commands.status_commands._prepare_http_service",
        return_value=("127.0.0.1", 47777, None),
    ) as mock_prepare:
        with patch(
            "sari.mcp.cli.commands.status_commands._request_http",
            return_value={"results": []},
        ) as mock_req:
            rc = cmd_search(args)
            assert rc == 0
            mock_prepare.assert_called_once_with(args, allow_mcp_fallback=False)
            mock_req.assert_called_once_with("/search", {"q": "hello", "limit": 3}, "127.0.0.1", 47777)


def test_cmd_status_uses_shared_preparation_helper_and_mcp_fallback(capsys):
    args = argparse.Namespace()
    with patch(
        "sari.mcp.cli.commands.status_commands._prepare_http_service",
        side_effect=RuntimeError("mcp-fallback"),
    ) as mock_prepare:
        with patch(
            "sari.mcp.cli.commands.status_commands._request_http",
            side_effect=AssertionError("HTTP should not be requested"),
        ):
            rc = cmd_status(args)
            assert rc == 1
            assert "mcp-fallback" in capsys.readouterr().out
            mock_prepare.assert_called_once_with(args, allow_mcp_fallback=True)

def test_cmd_daemon_stop():
    args = argparse.Namespace()
    with patch('sari.mcp.cli.daemon.read_pid', return_value=1234):
        with patch('os.kill') as mock_kill:
            with patch('sari.mcp.cli.daemon.ServerRegistry') as mock_registry_cls:
                mock_registry = MagicMock()
                mock_registry._load.return_value = {
                    "daemons": {"b1": {"host": "127.0.0.1", "port": 47779, "pid": 1234}},
                    "workspaces": {"/tmp/ws": {"boot_id": "b1", "http_pid": 4321}},
                }
                mock_registry_cls.return_value = mock_registry
                # First call: daemon is running, later calls: stopped
                with patch('sari.mcp.cli.daemon.is_daemon_running', side_effect=[True, False, False]):
                    with patch("sari.mcp.cli.daemon.kill_orphan_sari_workers", return_value=0) as mock_sweep:
                        ret = cmd_daemon_stop(args)
                        assert ret == 0
                        # daemon + http kill path should execute
                        assert mock_kill.called
                        mock_sweep.assert_called()


class _FakeProc:
    def __init__(self, pid, ppid, cmdline, env):
        self.info = {"pid": pid, "ppid": ppid, "cmdline": cmdline}
        self._env = env
        self.terminated = False
        self.killed = False

    def environ(self):
        return self._env

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return None

    def kill(self):
        self.killed = True


def test_kill_orphan_sari_workers_filters_and_kills(monkeypatch):
    from sari.mcp.cli import daemon as daemon_mod

    orphan_target = _FakeProc(
        1001,
        1,
        ["python", "-c", "from multiprocessing.spawn import spawn_main", "--multiprocessing-fork"],
        {"SARI_DAEMON_PORT": "47777", "SARI_WORKSPACE_ROOT": "/tmp/ws-a"},
    )
    non_orphan = _FakeProc(
        1002,
        4321,
        ["python", "-c", "from multiprocessing.spawn import spawn_main", "--multiprocessing-fork"],
        {"SARI_DAEMON_PORT": "47777", "SARI_WORKSPACE_ROOT": "/tmp/ws-a"},
    )
    other_port = _FakeProc(
        1003,
        1,
        ["python", "-c", "from multiprocessing.spawn import spawn_main", "--multiprocessing-fork"],
        {"SARI_DAEMON_PORT": "49999", "SARI_WORKSPACE_ROOT": "/tmp/ws-a"},
    )

    fake_psutil = type(
        "FakePsutil",
        (),
        {
            "process_iter": staticmethod(lambda *_args, **_kwargs: [orphan_target, non_orphan, other_port]),
            "NoSuchProcess": Exception,
            "AccessDenied": Exception,
            "ZombieProcess": Exception,
        },
    )()
    monkeypatch.setattr(daemon_mod, "psutil", fake_psutil)

    killed = daemon_mod.kill_orphan_sari_workers(port=47777)
    assert killed == 1
    assert orphan_target.terminated
    assert not non_orphan.terminated
    assert not other_port.terminated

def test_uninstall():
    from sari.uninstall import main as uninstall_main
    with patch('sys.argv', ['sari-uninstall']):
        with patch('builtins.input', return_value='y'):
            with patch('shutil.rmtree') as mock_rm:
                try:
                    uninstall_main()
                except SystemExit:
                    pass
                assert mock_rm.called


def test_cmd_daemon_refresh_stops_all_then_starts():
    args = argparse.Namespace(daemon_host="127.0.0.1", daemon_port=47779)
    with patch("sari.mcp.cli.commands.daemon_commands._cmd_daemon_stop_impl", return_value=0) as mock_stop:
        with patch("sari.mcp.cli.commands.daemon_commands._cmd_daemon_start_impl", return_value=0) as mock_start:
            rc = cmd_daemon_refresh(args)
            assert rc == 0
            mock_stop.assert_called_once()
            stop_args = mock_stop.call_args.args[0]
            assert stop_args.daemon_host == "127.0.0.1"
            assert stop_args.daemon_port == 47779
            mock_start.assert_called_once()


def test_cmd_daemon_start_daemonized_uses_lifecycle_lock():
    args = argparse.Namespace(daemonize=True, daemon_host="", daemon_port=None, http_host="", http_port=None)
    with patch("sari.mcp.cli.commands.daemon_commands.run_with_lifecycle_lock", return_value=7) as mock_lock:
        from sari.mcp.cli.commands.daemon_commands import cmd_daemon_start
        rc = cmd_daemon_start(args)
        assert rc == 7
        assert mock_lock.call_count == 1
        assert mock_lock.call_args.args[0] == "start"


def test_cmd_daemon_start_foreground_bypasses_lifecycle_lock():
    args = argparse.Namespace(daemonize=False, daemon_host="", daemon_port=None, http_host="", http_port=None)
    with patch("sari.mcp.cli.commands.daemon_commands.run_with_lifecycle_lock", return_value=7) as mock_lock:
        with patch("sari.mcp.cli.commands.daemon_commands._cmd_daemon_start_impl", return_value=0) as mock_start:
            from sari.mcp.cli.commands.daemon_commands import cmd_daemon_start
            rc = cmd_daemon_start(args)
            assert rc == 0
            assert mock_lock.call_count == 0
            mock_start.assert_called_once_with(args)


def test_cmd_daemon_stop_uses_lifecycle_lock():
    args = argparse.Namespace(all=True, daemon_host=None, daemon_port=None)
    with patch("sari.mcp.cli.commands.daemon_commands.run_with_lifecycle_lock", return_value=9) as mock_lock:
        from sari.mcp.cli.commands.daemon_commands import cmd_daemon_stop as _cmd_stop
        rc = _cmd_stop(args)
        assert rc == 9
        assert mock_lock.call_count == 1
        assert mock_lock.call_args.args[0] == "stop"


def test_cmd_daemon_refresh_uses_single_lifecycle_lock():
    args = argparse.Namespace(daemon_host="127.0.0.1", daemon_port=47779)
    with patch("sari.mcp.cli.commands.daemon_commands.run_with_lifecycle_lock", return_value=0) as mock_lock:
        rc = cmd_daemon_refresh(args)
        assert rc == 0
        assert mock_lock.call_count == 1
        assert mock_lock.call_args.args[0] == "refresh"


def test_legacy_commands_reexported_from_commands_modules():
    from sari.mcp.cli.commands import daemon_commands, status_commands, maintenance_commands
    import sari.mcp.cli.legacy_cli as legacy_cli

    pairs = [
        (legacy_cli.cmd_daemon_start, daemon_commands.cmd_daemon_start),
        (legacy_cli.cmd_daemon_stop, daemon_commands.cmd_daemon_stop),
        (legacy_cli.cmd_daemon_status, daemon_commands.cmd_daemon_status),
        (legacy_cli.cmd_daemon_ensure, daemon_commands.cmd_daemon_ensure),
        (legacy_cli.cmd_daemon_refresh, daemon_commands.cmd_daemon_refresh),
        (legacy_cli.cmd_status, status_commands.cmd_status),
        (legacy_cli.cmd_search, status_commands.cmd_search),
        (legacy_cli.cmd_doctor, maintenance_commands.cmd_doctor),
        (legacy_cli.cmd_init, maintenance_commands.cmd_init),
        (legacy_cli.cmd_prune, maintenance_commands.cmd_prune),
        (legacy_cli.cmd_vacuum, maintenance_commands.cmd_vacuum),
    ]
    for actual, expected in pairs:
        assert actual is expected


def test_ensure_workspace_http_sets_non_persistent_initialize():
    from sari.mcp.cli.mcp_client import ensure_workspace_http

    sent = {"raw": b""}
    response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode("utf-8")
    framed = f"Content-Length: {len(response)}\r\n\r\n".encode("ascii") + response

    class _FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def settimeout(self, _timeout):
            return None

        def sendall(self, data):
            sent["raw"] += data

        def makefile(self, _mode):
            return io.BytesIO(framed)

    with patch("socket.create_connection", return_value=_FakeSock()):
        ok = ensure_workspace_http("127.0.0.1", 47777, workspace_root="/tmp/ws")
        assert ok is True

    body = sent["raw"].split(b"\r\n\r\n", 1)[1]
    payload = json.loads(body.decode("utf-8"))
    assert payload["params"]["sariPersist"] is True


def test_start_daemon_background_uses_lazy_command_import():
    from sari.mcp.cli import daemon as daemon_mod

    # No explicit function injection should be required.
    daemon_mod._cmd_daemon_start_func = None
    with patch("sari.mcp.cli.commands.daemon_commands.cmd_daemon_start", return_value=0) as mock_start:
        ok = daemon_mod.start_daemon_background(daemon_host="127.0.0.1", daemon_port=47779)
        assert ok is True
        mock_start.assert_called_once()
