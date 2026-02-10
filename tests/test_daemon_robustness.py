import pytest
import time
import subprocess
import socket
import os
import sys
import unittest.mock as mock
from pathlib import Path
from sari.mcp.cli.smart_daemon import ensure_smart_daemon, is_port_in_use, smart_kill_port_owner
from sari.core.workspace import WorkspaceManager

def test_daemon_root_reuse_across_projects(tmp_path):
    """
    Scenario: A daemon is running on a port managing Project A.
    Expectation: ensure_smart_daemon should REUSE it for Project B instead of killing it.
    """
    root_a = str(tmp_path / "project_a")
    root_b = str(tmp_path / "project_b")
    
    host, port = "127.0.0.1", 9999
    
    # Mocking identity response from Project A
    identity_a = {"name": "sari", "version": "0.6.1", "workspaceRoot": root_a}
    
    with (
        mock.patch("sari.mcp.cli.smart_daemon.identify_sari_daemon", return_value=identity_a),
        mock.patch("sari.mcp.cli.smart_daemon.get_local_version", return_value="0.6.1"),
        mock.patch("sari.mcp.cli.smart_daemon.smart_kill_port_owner", return_value=True) as mock_kill,
        mock.patch("sari.mcp.cli.smart_daemon.is_port_in_use", return_value=True),
        mock.patch("sari.mcp.cli.smart_daemon.ensure_workspace_http", return_value=True) as mock_init,
        mock.patch("subprocess.Popen") as mock_popen
    ):
        # Current workspace is Project B
        ensure_smart_daemon(host, port, workspace_root=root_b)
        
        # Should NOT have killed the daemon because it's a valid Sari instance
        assert not mock_kill.called
        # Should HAVE initialized the new root B
        mock_init.assert_called_with(host, port, root_b)
        # Should NOT have started a new process
        assert not mock_popen.called

def test_smart_kill_protection_logic():
    """
    Verify smart_kill logic using a simple object to avoid MagicMock spec issues.
    """
    class MockAddr:
        def __init__(self, port): self.port = port

    class MockConn:
        def __init__(self, port): self.laddr = MockAddr(port)

    class FakeProcess:
        def __init__(self):
            self.pid = 1234
            self.terminate_called = False
        def cmdline(self): return ["python", "not_related.py"]
        def net_connections(self, kind="inet"): return [MockConn(9999)]
        def terminate(self): self.terminate_called = True
        def kill(self): pass

    fake_proc = FakeProcess()
    
    with (
        mock.patch("psutil.process_iter") as mock_iter,
        mock.patch("sari.mcp.cli.smart_daemon.is_port_in_use", return_value=True)
    ):
        mock_iter.return_value = [fake_proc]
        
        # Should NOT kill because it's not a Sari process (cmdline doesn't match)
        success = smart_kill_port_owner("127.0.0.1", 9999)
        
        assert success == False
        assert fake_proc.terminate_called == False

def test_environment_sync_verification():
    """
    Verify that the daemon is started with the correct PYTHONPATH and sys.executable.
    """
    with mock.patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = mock.MagicMock()
        
        with (
            mock.patch("sari.mcp.cli.smart_daemon.identify_sari_daemon", return_value=None),
            mock.patch("sari.mcp.cli.smart_daemon.is_port_in_use", return_value=False),
            mock.patch("sari.mcp.cli.smart_daemon.probe_sari_daemon", side_effect=[False, True]),
            mock.patch("sari.mcp.cli.smart_daemon.ensure_workspace_http", return_value=True)
        ):
            ensure_smart_daemon("127.0.0.1", 9999, workspace_root="/tmp/test_root")
            
            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            
            # Check executable
            assert args[0][0] == sys.executable
            
            # Check PYTHONPATH
            env = kwargs.get("env", {})
            assert "PYTHONPATH" in env
            import sari
            expected_path = str(Path(sari.__file__).parent.parent.resolve())
            assert expected_path in env["PYTHONPATH"]
