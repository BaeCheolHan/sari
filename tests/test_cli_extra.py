import pytest
import argparse
import sys
from unittest.mock import MagicMock, patch

# Mock psutil
sys.modules['psutil'] = MagicMock()

from sari.mcp.cli import cmd_doctor, cmd_search, cmd_daemon_start, cmd_daemon_stop, cmd_daemon_refresh

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
    with patch('sari.mcp.cli.commands.status_commands._request_http', return_value={"results": []}):
        with patch('sari.mcp.cli.commands.status_commands._get_http_host_port', return_value=("127.0.0.1", 47777)):
            ret = cmd_search(args)
            assert ret == 0

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
                except SystemExit: pass
                assert mock_rm.called


def test_cmd_daemon_refresh_stops_all_then_starts():
    args = argparse.Namespace(daemon_host="127.0.0.1", daemon_port=47779)
    with patch("sari.mcp.cli.commands.daemon_commands.cmd_daemon_stop", return_value=0) as mock_stop:
        with patch("sari.mcp.cli.commands.daemon_commands.cmd_daemon_start", return_value=0) as mock_start:
            rc = cmd_daemon_refresh(args)
            assert rc == 0
            mock_stop.assert_called_once()
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
