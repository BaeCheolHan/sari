import pytest
import argparse
import sys
from unittest.mock import MagicMock, patch
from sari.mcp.cli import cmd_daemon_status, cmd_init, cmd_prune, cmd_status, main
from sari.mcp.cli.commands.maintenance_commands import cmd_vacuum
from sari.main import main as sari_entry_main
from sari.main import run_cmd as sari_run_cmd

def test_cmd_daemon_status():
    args = argparse.Namespace(daemon_host="127.0.0.1", daemon_port=47779)
    with patch('sari.mcp.cli.commands.daemon_commands.is_daemon_running', return_value=True):
        with patch('sari.mcp.cli.commands.daemon_commands.identify_sari_daemon', return_value={"pid": 1234, "workspaceRoot": "/tmp/ws"}):
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
    
    with patch('sari.mcp.cli.commands.maintenance_commands.load_local_db', return_value=(db, [], "/tmp")):
        ret = cmd_prune(args)
        assert ret == 0
        assert db.prune_data.called


def test_cmd_vacuum():
    class _Conn:
        def __init__(self):
            self.sql = []

        def execute(self, q):
            self.sql.append(q)

    class _DB:
        def __init__(self):
            self.conn = _Conn()

        @property
        def db(self):
            class _Wrap:
                def __init__(self, conn):
                    self._conn = conn

                def connection(self):
                    return self._conn

            return _Wrap(self.conn)

        def close(self):
            pass

    db = _DB()
    args = argparse.Namespace(workspace=None)
    with patch("sari.mcp.cli.commands.maintenance_commands.load_local_db", return_value=(db, [], "/tmp")):
        ret = cmd_vacuum(args)
        assert ret == 0
        assert db.conn.sql == ["VACUUM"]

def test_cli_main_help():
    with patch('sys.argv', ['sari', '--help']):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["sari", "--cmd", "search", "--query", "needle", "--limit", "3"],
        ["sari", "--cmd", "status"],
    ],
)
def test_cli_main_cmd_routes_to_legacy_cli(argv):
    with patch("sari.mcp.cli.main", return_value=0) as mock_legacy:
        with patch("sys.argv", argv):
            rc = sari_entry_main()
            assert rc == 0
            mock_legacy.assert_called_once()


def test_cli_main_daemon_stop_all_parses():
    with patch("sari.mcp.cli.cmd_daemon_stop", return_value=0) as mock_stop:
        with patch("sys.argv", ["sari", "daemon", "stop", "--all"]):
            rc = main()
            assert rc == 0
            args = mock_stop.call_args.args[0]
            assert args.all is True


def test_run_cmd_does_not_mutate_sys_argv_for_legacy_routing():
    with patch("sari.mcp.cli.main", return_value=0) as mock_legacy:
        with patch("sys.argv", ["sari", "--sentinel"]):
            rc = sari_run_cmd(["status"])
            assert rc == 0
            assert mock_legacy.called
            assert sys.argv == ["sari", "--sentinel"]


def test_cli_main_search_command_dispatches_to_cmd_search():
    with patch("sari.mcp.cli.cmd_search", return_value=0) as mock_search:
        with patch("sys.argv", ["sari", "search", "--query", "needle", "--limit", "5"]):
            rc = main()
            assert rc == 0
            mock_search.assert_called_once()
            args = mock_search.call_args.args[0]
            assert args.query == "needle"
            assert args.limit == 5


def test_cmd_status_uses_resolved_non_default_daemon_port():
    args = argparse.Namespace(
        daemon_host=None,
        daemon_port=None,
        http_host=None,
        http_port=None,
    )
    with patch('sari.mcp.cli.commands.status_commands.get_daemon_address', return_value=("127.0.0.1", 47879)):
        with patch('sari.mcp.cli.commands.status_commands.is_daemon_running', return_value=True):
            with patch('sari.mcp.cli.commands.status_commands._resolve_http_endpoint_for_daemon', return_value=("127.0.0.1", 47777)):
                with patch('sari.mcp.cli.commands.status_commands._is_http_running', return_value=False):
                    with patch('sari.mcp.cli.commands.status_commands._ensure_workspace_http') as mock_ensure_ws:
                        with patch('sari.mcp.cli.commands.status_commands._request_mcp_status', return_value={"ok": True, "source": "mcp"}):
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
    with patch('sari.mcp.cli.commands.status_commands.get_daemon_address', return_value=("127.0.0.1", 47879)):
        with patch('sari.mcp.cli.commands.status_commands.is_daemon_running', return_value=False):
            with patch('sari.mcp.cli.commands.status_commands._resolve_http_endpoint_for_daemon', return_value=("127.0.0.1", 47777)):
                with patch('sari.mcp.cli.commands.status_commands._is_http_running', return_value=False):
                    with patch('sari.mcp.cli.commands.status_commands._ensure_daemon_running', return_value=("127.0.0.1", 47879, True)) as mock_ensure:
                        with patch('sari.mcp.cli.commands.status_commands._request_mcp_status', return_value={"ok": True, "source": "mcp"}):
                            rc = cmd_status(args)
                            assert rc == 0
                            mock_ensure.assert_called_once()
                            _, kwargs = mock_ensure.call_args
                            assert kwargs.get("allow_upgrade") is False


def test_cmd_status_prefers_registry_http_endpoint_for_selected_daemon():
    args = argparse.Namespace(
        daemon_host=None,
        daemon_port=None,
        http_host=None,
        http_port=None,
    )
    with patch("sari.mcp.cli.commands.status_commands.get_daemon_address", return_value=("127.0.0.1", 47879)):
        with patch("sari.mcp.cli.commands.status_commands.is_daemon_running", return_value=True):
            with patch(
                "sari.mcp.cli.commands.status_commands._resolve_http_endpoint_for_daemon",
                return_value=("127.0.0.1", 58155),
            ):
                with patch("sari.mcp.cli.commands.status_commands._is_http_running", return_value=True):
                    with patch("sari.mcp.cli.commands.status_commands._request_http", return_value={"ok": True}) as mock_request_http:
                        rc = cmd_status(args)
                        assert rc == 0
                        mock_request_http.assert_called_once_with("/status", {}, "127.0.0.1", 58155)
