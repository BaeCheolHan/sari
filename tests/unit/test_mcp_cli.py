import json
import os
import types
from pathlib import Path

import pytest

import mcp.cli as cli
import importlib


def test_is_loopback_and_enforce(monkeypatch):
    assert cli._is_loopback("127.0.0.1") is True
    assert cli._is_loopback("localhost") is True
    assert cli._is_loopback("0.0.0.0") is False

    monkeypatch.delenv("DECKARD_ALLOW_NON_LOOPBACK", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_ALLOW_NON_LOOPBACK", raising=False)
    with pytest.raises(RuntimeError):
        cli._enforce_loopback("0.0.0.0")

    monkeypatch.setenv("DECKARD_ALLOW_NON_LOOPBACK", "1")
    cli._enforce_loopback("0.0.0.0")


def test_get_http_host_port_env(monkeypatch):
    monkeypatch.setenv("DECKARD_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("DECKARD_HTTP_PORT", "9999")
    host, port = cli._get_http_host_port()
    assert host == "127.0.0.1"
    assert port == 9999


def test_get_daemon_address_env(monkeypatch):
    monkeypatch.setenv("DECKARD_DAEMON_HOST", "127.0.0.2")
    monkeypatch.setenv("DECKARD_DAEMON_PORT", "5555")
    host, port = cli.get_daemon_address()
    assert host == "127.0.0.2"
    assert port == 5555


def test_sys_path_insertion(monkeypatch):
    repo_root = str(cli.REPO_ROOT)
    if repo_root in cli.sys.path:
        cli.sys.path.remove(repo_root)
    importlib.reload(cli)
    assert repo_root in cli.sys.path


def test_load_config_and_server_info(tmp_path, monkeypatch):
    monkeypatch.delenv("DECKARD_HTTP_HOST", raising=False)
    monkeypatch.delenv("DECKARD_HTTP_PORT", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_HOST", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_PORT", raising=False)
    monkeypatch.delenv("DECKARD_HOST", raising=False)
    monkeypatch.delenv("DECKARD_PORT", raising=False)
    cfg = tmp_path / ".codex" / "tools" / "deckard" / "config" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"server_host": "127.0.0.1", "server_port": 1111}), encoding="utf-8")

    server_info = tmp_path / ".codex" / "tools" / "deckard" / "data" / "server.json"
    server_info.parent.mkdir(parents=True, exist_ok=True)
    server_info.write_text(json.dumps({"host": "127.0.0.1", "port": 2222}), encoding="utf-8")

    monkeypatch.setattr(cli.WorkspaceManager, "resolve_workspace_root", lambda: str(tmp_path))
    host, port = cli._get_http_host_port()
    assert port == 2222

    data = cli._load_http_config(str(tmp_path))
    assert data["server_port"] == 1111

    data = cli._load_server_info(str(tmp_path))
    assert data["port"] == 2222


def test_load_http_config_fallback_invalid(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{bad}", encoding="utf-8")
    monkeypatch.setattr(cli, "_package_config_path", lambda: bad)
    assert cli._load_http_config(str(tmp_path)) is None


def test_load_http_config_cfg_exception(tmp_path, monkeypatch):
    cfg = tmp_path / ".codex" / "tools" / "deckard" / "config" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{bad}", encoding="utf-8")
    monkeypatch.setattr(cli, "json", types.SimpleNamespace(loads=lambda *_: (_ for _ in ()).throw(ValueError("boom"))))
    assert cli._load_http_config(str(tmp_path)) is None


def test_load_http_config_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_package_config_path", lambda: tmp_path / "missing.json")
    assert cli._load_http_config(str(tmp_path)) is None


def test_load_server_info_invalid(tmp_path):
    server_info = tmp_path / ".codex" / "tools" / "deckard" / "data" / "server.json"
    server_info.parent.mkdir(parents=True, exist_ok=True)
    server_info.write_text("{bad}", encoding="utf-8")
    assert cli._load_server_info(str(tmp_path)) is None


def test_is_loopback_invalid_host():
    assert cli._is_loopback("999.999.999.999") is False


def test_request_http(monkeypatch):
    monkeypatch.setattr(cli, "_get_http_host_port", lambda: ("127.0.0.1", 1234))
    monkeypatch.setattr(cli, "_enforce_loopback", lambda host: None)

    class DummyResp:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *args, **kwargs: DummyResp())
    assert cli._request_http("/status", {})["ok"] is True


def test_get_http_host_port_registry_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.WorkspaceManager, "resolve_workspace_root", lambda: str(tmp_path))
    class BadRegistry:
        def get_instance(self, _root):
            raise RuntimeError("boom")
    monkeypatch.setattr(cli, "ServerRegistry", lambda: BadRegistry())
    host, port = cli._get_http_host_port()
    assert host == cli.DEFAULT_HTTP_HOST
    assert port == cli.DEFAULT_HTTP_PORT


def test_get_http_host_port_registry_priority(monkeypatch, tmp_path):
    monkeypatch.delenv("DECKARD_HTTP_HOST", raising=False)
    monkeypatch.delenv("DECKARD_HTTP_PORT", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_HOST", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_PORT", raising=False)
    monkeypatch.delenv("DECKARD_HOST", raising=False)
    monkeypatch.delenv("DECKARD_PORT", raising=False)
    monkeypatch.setattr(cli.WorkspaceManager, "resolve_workspace_root", lambda: str(tmp_path))
    class GoodRegistry:
        def get_instance(self, _root):
            return {"host": "127.0.0.3", "port": 3333}
    monkeypatch.setattr(cli, "ServerRegistry", lambda: GoodRegistry())
    host, port = cli._get_http_host_port()
    assert host == "127.0.0.3"
    assert port == 3333


def test_get_http_host_port_server_info_invalid(monkeypatch, tmp_path):
    monkeypatch.delenv("DECKARD_HTTP_HOST", raising=False)
    monkeypatch.delenv("DECKARD_HTTP_PORT", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_HOST", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_HTTP_PORT", raising=False)
    monkeypatch.delenv("DECKARD_HOST", raising=False)
    monkeypatch.delenv("DECKARD_PORT", raising=False)
    monkeypatch.setattr(cli.WorkspaceManager, "resolve_workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_load_server_info", lambda _root: {"host": "x", "port": "bad"})
    monkeypatch.setattr(cli, "_load_http_config", lambda _root: {"server_host": "h", "server_port": 1111})
    host, port = cli._get_http_host_port()
    assert host == "h"
    assert port == 1111


def test_is_daemon_running_false(monkeypatch):
    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()))
    assert cli.is_daemon_running("127.0.0.1", 1) is False


def test_is_daemon_running_true(monkeypatch):
    class DummySock:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: DummySock())
    assert cli.is_daemon_running("127.0.0.1", 1) is True


def test_pid_read_remove(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(cli, "PID_FILE", pid_file)
    pid_file.write_text("bad", encoding="utf-8")
    assert cli.read_pid() is None
    pid_file.write_text("123", encoding="utf-8")
    assert cli.read_pid() == 123
    cli.remove_pid()
    assert not pid_file.exists()


def test_daemon_start_stop_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: False)

    class DummyProc:
        pid = 123

    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: DummyProc())
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)

    args = types.SimpleNamespace(daemonize=True)
    assert cli.cmd_daemon_start(args) == 1

    # foreground start
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    args = types.SimpleNamespace(daemonize=False)
    assert cli.cmd_daemon_start(args) == 0

    # stop path without pid
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: False)
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0

    # status path
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    assert cli.cmd_daemon_status(types.SimpleNamespace()) == 0


def test_daemon_start_success(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    state = {"n": 0}
    def running(*_args, **_kwargs):
        state["n"] += 1
        return state["n"] > 1
    monkeypatch.setattr(cli, "is_daemon_running", running)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: types.SimpleNamespace(pid=999))
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    args = types.SimpleNamespace(daemonize=True)
    assert cli.cmd_daemon_start(args) == 0


def test_daemon_start_foreground_keyboardinterrupt(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: False)
    import asyncio
    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt()
    monkeypatch.setattr(asyncio, "run", fake_run)
    args = types.SimpleNamespace(daemonize=False)
    assert cli.cmd_daemon_start(args) == 0


def test_daemon_stop_windows_and_sigkill(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    monkeypatch.setattr(cli, "remove_pid", lambda: None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "time", types.SimpleNamespace(sleep=lambda *_: None))
    monkeypatch.setattr(cli, "os", types.SimpleNamespace(name="nt", kill=lambda *_: None))
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0

    # non-windows kill path
    monkeypatch.setattr(cli, "os", types.SimpleNamespace(name="posix", kill=lambda *_: None))
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0


def test_daemon_stop_exception(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    monkeypatch.setattr(cli, "remove_pid", lambda: None)
    monkeypatch.setattr(cli, "os", types.SimpleNamespace(name="posix", kill=lambda *_: (_ for _ in ()).throw(ProcessLookupError())))
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0


def test_daemon_stop_success_with_wait(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    state = {"n": 0}
    def running(*_args, **_kwargs):
        state["n"] += 1
        return state["n"] < 2
    monkeypatch.setattr(cli, "is_daemon_running", running)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    monkeypatch.setattr(cli, "remove_pid", lambda: None)
    monkeypatch.setattr(cli.os, "kill", lambda *_: None)
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0


def test_daemon_stop_no_pid(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: None)
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 1


def test_cmd_auto_and_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))

    class DummyErr(OSError):
        errno = 13

    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(DummyErr()))

    called = {"server": 0}
    def server_main():
        called["server"] += 1

    monkeypatch.setattr(cli, "cmd_proxy", lambda args: 0)
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "subprocess", cli.subprocess)

    import mcp.server as server_mod
    monkeypatch.setattr(server_mod, "main", server_main, raising=False)

    assert cli.cmd_auto(types.SimpleNamespace()) == 0
    assert called["server"] >= 1

    # status command when daemon not running
    monkeypatch.setattr(cli, "_get_http_host_port", lambda: ("127.0.0.1", 47777))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: False)
    assert cli.cmd_status(types.SimpleNamespace()) == 1


def test_cmd_daemon_status_socket_send(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    class DummySock:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def sendall(self, _data): return None
    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: DummySock())
    assert cli.cmd_daemon_status(types.SimpleNamespace()) == 0


def test_cmd_auto_proxy_path(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    class DummySock:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: DummySock())
    monkeypatch.setattr(cli, "cmd_proxy", lambda args: 0)
    assert cli.cmd_auto(types.SimpleNamespace()) == 0


def test_cmd_auto_daemon_start_path(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionRefusedError()))
    state = {"n": 0}
    def running(*_args, **_kwargs):
        state["n"] += 1
        return state["n"] > 1
    monkeypatch.setattr(cli, "is_daemon_running", running)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: types.SimpleNamespace(pid=999))
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    monkeypatch.setattr(cli, "cmd_proxy", lambda args: 0)
    assert cli.cmd_auto(types.SimpleNamespace()) == 0


def test_cmd_proxy(monkeypatch):
    called = {"n": 0}
    import mcp.proxy as proxy_mod
    monkeypatch.setattr(proxy_mod, "main", lambda: called.__setitem__("n", 1), raising=False)
    cli.cmd_proxy(types.SimpleNamespace())
    assert called["n"] == 1


def test_cmd_search_and_init(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_request_http", lambda *args, **kwargs: {"ok": True})

    args = types.SimpleNamespace(query="q", limit=2, repo="")
    assert cli.cmd_search(args) == 0

    args = types.SimpleNamespace(query="q", limit=2, repo="repo1")
    assert cli.cmd_search(args) == 0

    args = types.SimpleNamespace(workspace=str(tmp_path), force=True, no_marker=False)
    assert cli.cmd_init(args) == 0

    # marker exists + config exists path
    args = types.SimpleNamespace(workspace=str(tmp_path), force=False, no_marker=False)
    assert cli.cmd_init(args) == 0

    # no marker path
    args = types.SimpleNamespace(workspace=str(tmp_path), force=False, no_marker=True)
    assert cli.cmd_init(args) == 0


def test_main_help(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["deckard"])
    assert cli.main() == 1


def test_main_daemon_help(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["deckard", "daemon"])
    assert cli.main() == 1


def test_cmd_daemon_stop_with_pid(monkeypatch):
    monkeypatch.setattr(cli, "get_daemon_address", lambda: ("127.0.0.1", 5000))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "read_pid", lambda: 123)
    monkeypatch.setattr(cli, "remove_pid", lambda: None)
    monkeypatch.setattr(cli.os, "kill", lambda *_: None)
    monkeypatch.setattr(cli.time, "sleep", lambda *_: None)
    assert cli.cmd_daemon_stop(types.SimpleNamespace()) == 0


def test_cmd_status_success(monkeypatch):
    monkeypatch.setattr(cli, "_get_http_host_port", lambda: ("127.0.0.1", 47777))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_request_http", lambda *args, **kwargs: {"ok": True})
    assert cli.cmd_status(types.SimpleNamespace()) == 0


def test_cmd_status_exception(monkeypatch):
    monkeypatch.setattr(cli, "_get_http_host_port", lambda: ("127.0.0.1", 47777))
    monkeypatch.setattr(cli, "is_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_request_http", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cli.cmd_status(types.SimpleNamespace()) == 1


def test_main_invokes_func(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["deckard", "status"])
    monkeypatch.setattr(cli, "cmd_status", lambda _args: 0)
    assert cli.main() == 0


def test_main_module_entry(monkeypatch):
    import runpy
    import sys
    sys.modules.pop("mcp.cli", None)
    monkeypatch.setattr(cli.sys, "argv", ["deckard"])
    monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        runpy.run_module("mcp.cli", run_name="__main__")
