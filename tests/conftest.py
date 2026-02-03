import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _deckard_test_isolation(monkeypatch, tmp_path):
    """
    Hard isolation so unit tests never touch a real running daemon or real
    user directories. Tests can explicitly override with monkeypatch if needed.
    """
    # Force testing mode + local-only endpoints
    monkeypatch.setenv("DECKARD_TESTING", "1")
    monkeypatch.setenv("DECKARD_DAEMON_HOST", "127.0.0.1")
    monkeypatch.setenv("DECKARD_DAEMON_PORT", "0")
    monkeypatch.setenv("DECKARD_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("DECKARD_HTTP_PORT", "0")
    monkeypatch.setenv("DECKARD_ALLOW_NON_LOOPBACK", "0")
    monkeypatch.setenv("LOCAL_SEARCH_ALLOW_NON_LOOPBACK", "0")

    # Isolate workspace/log dirs to temp
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DECKARD_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("LOCAL_SEARCH_WORKSPACE_ROOT", str(workspace_root))
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DECKARD_LOG_DIR", str(log_dir))

    # Block real sockets unless test explicitly overrides
    def _blocked_socket(*_args, **_kwargs):
        raise RuntimeError("Test isolation: socket.create_connection blocked (mock it in test).")

    monkeypatch.setattr(socket, "create_connection", _blocked_socket)

    # Block real subprocess spawn unless test explicitly overrides
    def _blocked_popen(*_args, **_kwargs):
        raise RuntimeError("Test isolation: subprocess.Popen blocked (mock it in test).")

    monkeypatch.setattr(subprocess, "Popen", _blocked_popen)

    # Block real sys.exit so accidental exits don't terminate test run
    def _blocked_exit(code=0):
        raise RuntimeError(f"Test isolation: sys.exit blocked (code={code}).")

    monkeypatch.setattr(sys, "exit", _blocked_exit)
