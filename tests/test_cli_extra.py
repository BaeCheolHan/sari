import pytest
import argparse
import sys
from unittest.mock import MagicMock, patch

# Mock psutil
sys.modules['psutil'] = MagicMock()

from sari.mcp.cli import cmd_doctor, cmd_search, cmd_daemon_start, cmd_daemon_stop

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
    with patch('sari.mcp.cli.legacy_cli._request_http', return_value={"results": []}):
        with patch('sari.mcp.cli.legacy_cli._get_http_host_port', return_value=("127.0.0.1", 47777)):
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
