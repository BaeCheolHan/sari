import argparse
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
                    ret = cmd_daemon_stop(args)
                    assert ret == 0
                    # daemon + http kill path should execute
                    assert mock_kill.called

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
    with patch("sari.mcp.cli.commands.daemon_commands.cmd_daemon_stop", return_value=0) as mock_stop:
        with patch("sari.mcp.cli.commands.daemon_commands.cmd_daemon_start", return_value=0) as mock_start:
            rc = cmd_daemon_refresh(args)
            assert rc == 0
            mock_stop.assert_called_once()
            stop_args = mock_stop.call_args.args[0]
            assert stop_args.daemon_host == "127.0.0.1"
            assert stop_args.daemon_port == 47779
            mock_start.assert_called_once()


def test_legacy_daemon_commands_reexported_from_commands_module():
    from sari.mcp.cli.commands import daemon_commands
    import sari.mcp.cli.legacy_cli as legacy_cli

    assert legacy_cli.cmd_daemon_start is daemon_commands.cmd_daemon_start
    assert legacy_cli.cmd_daemon_stop is daemon_commands.cmd_daemon_stop
    assert legacy_cli.cmd_daemon_status is daemon_commands.cmd_daemon_status
    assert legacy_cli.cmd_daemon_ensure is daemon_commands.cmd_daemon_ensure
    assert legacy_cli.cmd_daemon_refresh is daemon_commands.cmd_daemon_refresh


def test_legacy_status_and_maintenance_commands_reexported_from_commands_module():
    from sari.mcp.cli.commands import status_commands, maintenance_commands
    import sari.mcp.cli.legacy_cli as legacy_cli

    assert legacy_cli.cmd_status is status_commands.cmd_status
    assert legacy_cli.cmd_search is status_commands.cmd_search
    assert legacy_cli.cmd_doctor is maintenance_commands.cmd_doctor
    assert legacy_cli.cmd_init is maintenance_commands.cmd_init
    assert legacy_cli.cmd_prune is maintenance_commands.cmd_prune
    assert legacy_cli.cmd_vacuum is maintenance_commands.cmd_vacuum
