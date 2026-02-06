import pytest
import argparse
from unittest.mock import MagicMock, patch
from sari.mcp.cli import cmd_daemon_status, cmd_init, cmd_prune, cmd_status, main

def test_cmd_daemon_status():
    args = argparse.Namespace()
    with patch('sari.mcp.cli.is_daemon_running', return_value=True):
        with patch('sari.mcp.cli.read_pid', return_value=1234):
            with patch('sari.mcp.cli.get_daemon_address', return_value=("127.0.0.1", 47779)):
                ret = cmd_daemon_status(args)
                assert ret == 0

def test_cmd_init(tmp_path):
    args = argparse.Namespace(workspace=str(tmp_path), force=False)
    with patch('sari.core.workspace.WorkspaceManager.resolve_config_path', return_value=str(tmp_path / "config.json")):
        with patch('sari.core.workspace.WorkspaceManager.resolve_workspace_root', return_value=str(tmp_path)):
            ret = cmd_init(args)
            assert ret == 0
            assert (tmp_path / "config.json").exists()

def test_cmd_prune():
    db = MagicMock()
    db.prune_data.return_value = 5
    args = argparse.Namespace(table="snippets", days=30, workspace=None)
    
    with patch('sari.mcp.cli._load_local_db', return_value=(db, [], "/tmp")):
        ret = cmd_prune(args)
        assert ret == 0
        assert db.prune_data.called

def test_cli_main_help():
    with patch('sys.argv', ['sari', '--help']):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 0


def test_cmd_status_uses_resolved_non_default_daemon_port():
    args = argparse.Namespace(
        daemon_host=None,
        daemon_port=None,
        http_host=None,
        http_port=None,
    )
    with patch('sari.mcp.cli.get_daemon_address', return_value=("127.0.0.1", 47879)):
        with patch('sari.mcp.cli.is_daemon_running', return_value=True):
            with patch('sari.mcp.cli._get_http_host_port', return_value=("127.0.0.1", 47777)):
                with patch('sari.mcp.cli._is_http_running', return_value=False):
                    with patch('sari.mcp.cli._ensure_workspace_http') as mock_ensure_ws:
                        with patch('sari.mcp.cli._request_mcp_status', return_value={"ok": True, "source": "mcp"}):
                            rc = cmd_status(args)
                            assert rc == 0
                            # Must keep using the resolved daemon port (not hard-coded default).
                            mock_ensure_ws.assert_called_with("127.0.0.1", 47879)


def test_cmd_status_starts_daemon_on_resolved_non_default_port():
    args = argparse.Namespace(
        daemon_host=None,
        daemon_port=None,
        http_host=None,
        http_port=None,
    )
    with patch('sari.mcp.cli.get_daemon_address', return_value=("127.0.0.1", 47879)):
        with patch('sari.mcp.cli.is_daemon_running', return_value=False):
            with patch('sari.mcp.cli._get_http_host_port', return_value=("127.0.0.1", 47777)):
                with patch('sari.mcp.cli._is_http_running', return_value=False):
                    with patch('sari.mcp.cli._ensure_daemon_running', return_value=("127.0.0.1", 47879, True)) as mock_ensure:
                        with patch('sari.mcp.cli._request_mcp_status', return_value={"ok": True, "source": "mcp"}):
                            rc = cmd_status(args)
                            assert rc == 0
                            mock_ensure.assert_called_once()
                            _, kwargs = mock_ensure.call_args
                            assert kwargs.get("allow_upgrade") is False
