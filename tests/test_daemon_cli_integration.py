import pytest
import subprocess
import time
import json
import os
from pathlib import Path

def test_cli_daemon_lifecycle_truth(tmp_path):
    """
    Verify that the CLI can actually start and stop the daemon.
    Truth: The daemon must respond to 'status' command after start.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".sari").mkdir()
    
    env = os.environ.copy()
    env["SARI_WORKSPACE_ROOT"] = str(workspace)
    env["SARI_DAEMON_PORT"] = "48999"
    
    # 1. Start Daemon
    start_proc = subprocess.run(
        ["python3", "-m", "sari.mcp.cli", "daemon", "start", "-d", "--daemon-port", "48999"],
        env=env, capture_output=True, text=True
    )
    assert start_proc.returncode == 0
    time.sleep(2) # Grace period for boot
    
    # 2. Check Status
    status_proc = subprocess.run(
        ["python3", "-m", "sari.mcp.cli", "daemon", "status", "--daemon-port", "48999"],
        env=env, capture_output=True, text=True
    )
    # Check for Running emoji or text
    assert "Running" in status_proc.stdout
    
    # 3. Stop Daemon
    stop_proc = subprocess.run(
        ["python3", "-m", "sari.mcp.cli", "daemon", "stop", "--daemon-port", "48999"],
        env=env, capture_output=True, text=True
    )
    assert stop_proc.returncode == 0
