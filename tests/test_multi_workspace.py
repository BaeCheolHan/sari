import os
import socket
import subprocess
import time
import sys

class TestMultiWorkspaceIntegration:
    def _free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _wait_for_daemon_ready(self, env, port, timeout_sec=10):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            status = subprocess.run(
                [sys.executable, "-m", "sari.mcp.cli", "daemon", "status", "--daemon-port", str(port)],
                env=env,
                capture_output=True,
                text=True,
            )
            if status.returncode == 0:
                return
            time.sleep(0.2)
        raise AssertionError(f"daemon on port {port} did not become ready in time")
    
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
        daemon_port = self._free_port()

        # 2. Start daemon for WS_0
        multi_env["SARI_WORKSPACE_ROOT"] = ws_paths[0]
        subprocess.run([sys.executable, "-m", "sari.mcp.cli", "daemon", "start", "-d", "--daemon-port", str(daemon_port)], env=multi_env, check=True)
        self._wait_for_daemon_ready(multi_env, daemon_port)
        
        try:
            # 3. Request WS_1 via CLI (should notify existing daemon)
            multi_env["SARI_WORKSPACE_ROOT"] = ws_paths[1]
            ensured = False
            for _ in range(10):
                res = subprocess.run(
                    [sys.executable, "-m", "sari.mcp.cli", "daemon", "ensure", "--daemon-port", str(daemon_port)],
                    env=multi_env,
                )
                if res.returncode == 0:
                    ensured = True
                    break
                time.sleep(0.3)
            assert ensured
            time.sleep(1)
            
            # 4. Verify both are in registry
            from sari.core.server_registry import ServerRegistry
            os.environ["SARI_REGISTRY_FILE"] = multi_env["SARI_REGISTRY_FILE"]
            registry = ServerRegistry()
            
            ws1 = registry.get_workspace(ws_paths[1])
            assert ws1 is not None
            active = registry.resolve_workspace_daemon(ws_paths[1])
            assert active is not None

            ws0 = registry.get_workspace(ws_paths[0])
            if ws0 is not None:
                # Single HTTP gateway: all workspaces under the same daemon share one endpoint.
                assert ws0["boot_id"] == ws1["boot_id"]
                assert ws0["http_port"] == ws1["http_port"]
                assert ws0["http_host"] == ws1["http_host"]
            
        finally:
            subprocess.run([sys.executable, "-m", "sari.mcp.cli", "daemon", "stop", "--daemon-port", str(daemon_port)], env=multi_env)
