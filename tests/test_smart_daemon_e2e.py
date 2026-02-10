import pytest
import time
import subprocess
import socket
import os
import sys
import psutil
from sari.mcp.cli.smart_daemon import ensure_smart_daemon, is_port_in_use, smart_kill_port_owner

def test_is_port_in_use_logic():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    
    assert is_port_in_use("127.0.0.1", free_port) == False
    
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", free_port))
    s2.listen(1)
    try:
        assert is_port_in_use("127.0.0.1", free_port) == True
    finally:
        s2.close()

def test_smart_kill_basic():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    
    test_script = "sari_test_stale.py"
    with open(test_script, "w") as f:
        # Fix escaping here
        f.write(f"import socket, time; s=socket.socket(); s.bind((\"127.0.0.1\", {port})); s.listen(1); time.sleep(10)")
    
    proc = subprocess.Popen([sys.executable, test_script])
    try:
        time.sleep(2) # Give it more time
        if not is_port_in_use("127.0.0.1", port):
            # Check if it crashed
            stdout, stderr = proc.communicate(timeout=0.1)
            pytest.fail(f"Test script failed to start. stderr: {stderr}")

        assert is_port_in_use("127.0.0.1", port) == True
        
        success = smart_kill_port_owner("127.0.0.1", port)
        assert success == True
        assert is_port_in_use("127.0.0.1", port) == False
    finally:
        proc.kill()
        if os.path.exists(test_script):
            os.remove(test_script)

def test_ensure_smart_daemon_e2e():
    # Use a dynamic port to avoid conflicts
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    host = "127.0.0.1"
    
    # Ensure it is NOT running
    smart_kill_port_owner(host, port)
    assert not is_port_in_use(host, port)
    
    # Run ensure_smart_daemon
    # Explicitly set SARI_DAEMON_PORT so it starts on the requested port
    os.environ["SARI_DAEMON_PORT"] = str(port)
    try:
        h, p = ensure_smart_daemon(host, port)
        
        assert h == host
        assert p == port
        assert is_port_in_use(host, port) == True
    finally:
        # Cleanup
        smart_kill_port_owner(host, port)
        if "SARI_DAEMON_PORT" in os.environ: del os.environ["SARI_DAEMON_PORT"]
