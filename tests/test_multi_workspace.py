import json
import os
import subprocess
import time
import pytest
import sys
from pathlib import Path

class TestMultiWorkspaceIntegration:
    
    def test_daemon_handles_multiple_workspaces(self, tmp_path):
        # 1. Setup multiple workspaces
        ws_paths = []
        for i in range(2):
            p = tmp_path / f"ws_{i}"
            p.mkdir()
            (p / ".sari").mkdir()
            (p / "main.py").write_text(f"print('ws_{i}')")
            ws_paths.append(str(p))
            
        multi_env = os.environ.copy()
        multi_env["SARI_REGISTRY_FILE"] = str(tmp_path / "multi_registry.json")
        multi_env["PYTHONPATH"] = os.pathsep.join([os.path.abspath("src")] + sys.path)
        
        # 2. Start daemon for WS_0
        multi_env["SARI_WORKSPACE_ROOT"] = ws_paths[0]
        subprocess.run([sys.executable, "-m", "sari.mcp.cli", "daemon", "start", "-d", "--daemon-port", "48100"], env=multi_env, check=True)
        time.sleep(1)
        
        try:
            # 3. Request WS_1 via CLI (should notify existing daemon)
            multi_env["SARI_WORKSPACE_ROOT"] = ws_paths[1]
            subprocess.run([sys.executable, "-m", "sari.mcp.cli", "daemon", "ensure", "--daemon-port", "48100"], env=multi_env, check=True)
            time.sleep(2)
            
            # 4. Verify both are in registry
            from sari.core.server_registry import ServerRegistry
            os.environ["SARI_REGISTRY_FILE"] = multi_env["SARI_REGISTRY_FILE"]
            registry = ServerRegistry()
            
            ws0 = registry.get_workspace(ws_paths[0])
            ws1 = registry.get_workspace(ws_paths[1])
            
            assert ws0 is not None
            assert ws1 is not None
            # Single HTTP gateway: all workspaces under the same daemon share one endpoint.
            assert ws0["boot_id"] == ws1["boot_id"]
            assert ws0["http_port"] == ws1["http_port"]
            assert ws0["http_host"] == ws1["http_host"]
            
        finally:
            subprocess.run([sys.executable, "-m", "sari.mcp.cli", "daemon", "stop", "--daemon-port", "48100"], env=multi_env)
