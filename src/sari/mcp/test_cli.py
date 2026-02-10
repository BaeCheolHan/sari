#!/usr/bin/env python3
# ruff: noqa: E402
"""
Unit tests for Sari CLI HTTP helpers.
"""
import io
import json
import os
import sys
import tempfile
import importlib
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from cli import _get_http_host_port, cmd_search, cmd_status


def _set_env(key: str, value: str):
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def test_get_http_host_port_prefers_server_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / ".codex" / "tools" / "sari" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        server_json = data_dir / "server.json"
        server_json.write_text(json.dumps({"host": "127.0.0.1", "port": 47788}))

        prev = os.environ.get("SARI_WORKSPACE_ROOT")
        _set_env("SARI_WORKSPACE_ROOT", tmpdir)
        try:
            host, port = _get_http_host_port()
            assert host == "127.0.0.1"
            assert port == 47788
        finally:
            _set_env("SARI_WORKSPACE_ROOT", prev)


def test_get_http_host_port_prefers_registry_when_no_server_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir).resolve()
        reg_path = Path(tmpdir) / "registry.json"
        data = {
            "version": "2.0",
            "daemons": {},
            "workspaces": {
                str(ws_root): {"boot_id": "boot-x", "http_port": 47799, "http_host": "127.0.0.1"}
            },
        }
        reg_path.write_text(json.dumps(data), encoding="utf-8")

        prev_root = os.environ.get("SARI_WORKSPACE_ROOT")
        prev_reg = os.environ.get("SARI_REGISTRY_FILE")
        _set_env("SARI_WORKSPACE_ROOT", str(ws_root))
        _set_env("SARI_REGISTRY_FILE", str(reg_path))
        try:
            # Update registry module globals used by ServerRegistry.
            from sari.core import registry as core_registry
            core_registry.REGISTRY_FILE = reg_path
            core_registry.REGISTRY_DIR = reg_path.parent
            core_registry.LOCK_FILE = reg_path.parent / "server.json.lock"
            host, port = _get_http_host_port()
            assert host == "127.0.0.1"
            assert port == 47799
        finally:
            _set_env("SARI_WORKSPACE_ROOT", prev_root)
            _set_env("SARI_REGISTRY_FILE", prev_reg)


def test_cmd_status_prints_json():
    mod = importlib.import_module(cmd_status.__module__)
    with patch(f"{mod.__name__}._request_http", return_value={"ok": True}) as mock_req, \
         patch(f"{mod.__name__}.is_daemon_running", return_value=True), \
         patch(f"{mod.__name__}._resolve_http_endpoint_for_daemon", return_value=("127.0.0.1", 47777)), \
         patch(f"{mod.__name__}._is_http_running", return_value=True):
        buf = io.StringIO()
        with redirect_stdout(buf):
            args = type("Args", (), {"daemon_host": "", "daemon_port": None, "http_host": "", "http_port": None})
            rc = cmd_status(args)
        assert rc == 0
        mock_req.assert_called_once_with("/status", {}, "127.0.0.1", 47777)
        out = buf.getvalue().strip()
        assert out == json.dumps({"ok": True}, ensure_ascii=False, indent=2)


def test_cmd_search_prints_json():
    args = type("Args", (), {"query": "AuthService", "repo": "demo", "limit": 7, "daemon_host": "", "daemon_port": None, "http_host": "", "http_port": None})
    mod = importlib.import_module(cmd_search.__module__)
    with patch(f"{mod.__name__}._request_http", return_value={"ok": True, "q": "AuthService"}) as mock_req, \
         patch(f"{mod.__name__}.is_daemon_running", return_value=True), \
         patch(f"{mod.__name__}._resolve_http_endpoint_for_daemon", return_value=("127.0.0.1", 47777)), \
         patch(f"{mod.__name__}._is_http_running", return_value=True):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_search(args)
        assert rc == 0
        mock_req.assert_called_once_with("/search", {"q": "AuthService", "limit": 7, "repo": "demo"}, "127.0.0.1", 47777)
        out = buf.getvalue().strip()
        assert out == json.dumps({"ok": True, "q": "AuthService"}, ensure_ascii=False, indent=2)


def run_tests():
    tests = [
        test_get_http_host_port_prefers_server_json,
        test_get_http_host_port_prefers_registry_when_no_server_json,
        test_cmd_status_prints_json,
        test_cmd_search_prints_json,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
            passed += 1
        except Exception:
            failed += 1
            raise
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
