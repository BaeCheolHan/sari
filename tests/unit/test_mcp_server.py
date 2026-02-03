import io
import json
import os
import types
from pathlib import Path

import pytest

import mcp.server as server_mod
import importlib
import io
import runpy


class DummyLogger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.telemetry = []

    def log_info(self, msg):
        self.infos.append(msg)

    def log_error(self, msg):
        self.errors.append(msg)

    def log_telemetry(self, msg):
        self.telemetry.append(msg)


class DummyIndexer:
    def __init__(self):
        self.status = types.SimpleNamespace(index_ready=True)
        self.stopped = False

    def run_forever(self):
        return None

    def stop(self):
        self.stopped = True


class DummyDB:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_resolve_version_env_and_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKARD_VERSION", "1.2.3")
    assert server_mod.LocalSearchMCPServer._resolve_version() == "1.2.3"

    monkeypatch.delenv("DECKARD_VERSION", raising=False)
    ver_path = tmp_path / "VERSION"
    ver_path.write_text("2.0.0", encoding="utf-8")
    monkeypatch.setattr(server_mod, "REPO_ROOT", tmp_path)
    assert server_mod.LocalSearchMCPServer._resolve_version() == "2.0.0"
    # sys.path insertion path
    repo_root = str(server_mod.REPO_ROOT)
    if repo_root in server_mod.sys.path:
        server_mod.sys.path.remove(repo_root)
    importlib.reload(server_mod)
    assert str(server_mod.REPO_ROOT) in server_mod.sys.path


def test_resolve_version_read_error(monkeypatch, tmp_path):
    monkeypatch.delenv("DECKARD_VERSION", raising=False)
    ver_path = tmp_path / "VERSION"
    ver_path.write_text("2.0.0", encoding="utf-8")
    monkeypatch.setattr(server_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(server_mod.Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert server_mod.LocalSearchMCPServer._resolve_version() == "dev"


def test_search_first_policy(monkeypatch):
    monkeypatch.setenv("DECKARD_SEARCH_FIRST_MODE", "enforce")
    assert server_mod.LocalSearchMCPServer._resolve_search_first_policy() == "enforce"
    monkeypatch.delenv("DECKARD_SEARCH_FIRST_MODE", raising=False)

    monkeypatch.setenv("DECKARD_ENFORCE_SEARCH_FIRST", "0")
    assert server_mod.LocalSearchMCPServer._resolve_search_first_policy() == "off"
    monkeypatch.setenv("DECKARD_ENFORCE_SEARCH_FIRST", "1")
    assert server_mod.LocalSearchMCPServer._resolve_search_first_policy() == "enforce"


def test_search_first_warning_and_error(monkeypatch):
    server = server_mod.LocalSearchMCPServer("/tmp")
    server.logger = DummyLogger()
    server._search_first_mode = "enforce"
    err = server._search_first_error()
    assert err.get("isError") is True

    server._search_first_mode = "warn"
    result = server._search_first_warning({"content": []})
    assert "warnings" in result

    class BadLogger(DummyLogger):
        def log_telemetry(self, msg):
            raise RuntimeError("boom")

    server.logger = BadLogger()
    server._search_first_error()
    server._search_first_warning({})


def test_handle_initialize_and_request(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()

    class DummyWM:
        @staticmethod
        def resolve_workspace_root(arg=None):
            return str(tmp_path)

    monkeypatch.setattr(server_mod, "WorkspaceManager", DummyWM)

    res = server.handle_initialize({"rootUri": str(tmp_path)})
    assert res["serverInfo"]["name"] == "deckard"

    # handle_initialize log error path
    class BadLogger(DummyLogger):
        def log_info(self, msg):
            raise RuntimeError("boom")
        def log_error(self, msg):
            self.errors.append(msg)

    server.logger = BadLogger()
    server.handle_initialize({"rootUri": str(tmp_path)})

    # Unknown method
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert resp["error"]["code"] == -32601

    # Notification unknown method -> None
    resp = server.handle_request({"jsonrpc": "2.0", "method": "nope"})
    assert resp is None


def test_handle_initialize_workspace_change(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.workspace_root = "/old"

    class DummyWM:
        @staticmethod
        def resolve_workspace_root(arg=None):
            return "/new"

    monkeypatch.setattr(server_mod, "WorkspaceManager", DummyWM)
    server.handle_initialize({"rootUri": "/new"})
    assert server.workspace_root == "/new"


def test_handle_initialized_triggers_init(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    called = {"n": 0}
    monkeypatch.setattr(server, "_ensure_initialized", lambda: called.__setitem__("n", called["n"] + 1))
    server.handle_initialized({})
    assert called["n"] == 1


def test_handle_tools_list_and_dispatch(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True
    server.db = DummyDB()
    server.indexer = DummyIndexer()
    server.cfg = types.SimpleNamespace()

    tools = server.handle_tools_list({})
    assert "tools" in tools

    monkeypatch.setattr(server_mod.deckard_guide_tool, "execute_deckard_guide", lambda args: {"ok": True})
    monkeypatch.setattr(server_mod.search_tool, "execute_search", lambda args, db, logger: {"ok": True})
    monkeypatch.setattr(server_mod.status_tool, "execute_status", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.repo_candidates_tool, "execute_repo_candidates", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.list_files_tool, "execute_list_files", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.read_file_tool, "execute_read_file", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.search_symbols_tool, "execute_search_symbols", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.read_symbol_tool, "execute_read_symbol", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.doctor_tool, "execute_doctor", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.search_api_endpoints_tool, "execute_search_api_endpoints", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.index_file_tool, "execute_index_file", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.rescan_tool, "execute_rescan", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.scan_once_tool, "execute_scan_once", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.get_callers_tool, "execute_get_callers", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.get_implementations_tool, "execute_get_implementations", lambda *args, **kwargs: {"ok": True})

    for name, args in [
        ("deckard_guide", {}),
        ("search", {"query": "x"}),
        ("status", {}),
        ("repo_candidates", {"query": "x"}),
        ("list_files", {}),
        ("read_file", {"path": "x"}),
        ("search_symbols", {"query": "x"}),
        ("read_symbol", {"path": "x", "name": "y"}),
        ("doctor", {}),
        ("search_api_endpoints", {"path": "/api"}),
        ("index_file", {"path": "x"}),
        ("rescan", {}),
        ("scan_once", {}),
        ("get_callers", {"name": "x"}),
        ("get_implementations", {"name": "x"}),
    ]:
        assert server.handle_tools_call({"name": name, "arguments": args}).get("ok") is True


def test_search_first_warning_read_paths(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True
    server.db = DummyDB()
    server.indexer = DummyIndexer()

    monkeypatch.setattr(server_mod.read_file_tool, "execute_read_file", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.read_symbol_tool, "execute_read_symbol", lambda *args, **kwargs: {"ok": True})

    server._search_first_mode = "warn"
    result = server._tool_read_file({"path": "x"})
    assert "warnings" in result
    result = server._tool_read_symbol({"path": "x", "name": "y"})
    assert "warnings" in result


def test_tool_doctor_payload(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True
    server.db = DummyDB()
    server.indexer = DummyIndexer()
    server._search_first_mode = "warn"
    server._search_usage["search"] = 1

    captured = {}
    def fake_doctor(payload):
        captured.update(payload)
        return {"ok": True}

    monkeypatch.setattr(server_mod.doctor_tool, "execute_doctor", fake_doctor)
    result = server._tool_doctor({})
    assert result["ok"] is True
    assert captured["search_first_mode"] == "warn"


def test_handle_request_paths(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True

    monkeypatch.setattr(server, "handle_initialize", lambda params: {"ok": True})
    monkeypatch.setattr(server, "handle_initialized", lambda params: None)
    monkeypatch.setattr(server, "handle_tools_list", lambda params: {"tools": []})
    monkeypatch.setattr(server, "handle_tools_call", lambda params: {"ok": True})

    assert server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["ok"] is True
    assert server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "initialized", "params": {}}) is None
    assert server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})["result"]["tools"] == []
    assert server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}})["result"] == {}

    # Notification path returns None
    assert server.handle_request({"jsonrpc": "2.0", "method": "tools/list", "params": {}}) is None

    # Notification error path returns None
    monkeypatch.setattr(server, "handle_tools_call", lambda params: (_ for _ in ()).throw(RuntimeError("boom")))
    assert server.handle_request({"jsonrpc": "2.0", "method": "tools/call", "params": {}}) is None


def test_handle_tools_call_and_search_first(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()

    # skip actual init
    server._initialized = True
    server.db = DummyDB()
    server.indexer = DummyIndexer()
    server.cfg = types.SimpleNamespace()

    monkeypatch.setattr(server_mod.search_tool, "execute_search", lambda args, db, logger: {"ok": True})
    monkeypatch.setattr(server_mod.status_tool, "execute_status", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.repo_candidates_tool, "execute_repo_candidates", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.list_files_tool, "execute_list_files", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.read_file_tool, "execute_read_file", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.search_symbols_tool, "execute_search_symbols", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.read_symbol_tool, "execute_read_symbol", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(server_mod.doctor_tool, "execute_doctor", lambda *args, **kwargs: {"ok": True})

    server._search_first_mode = "enforce"
    result = server.handle_tools_call({"name": "read_file", "arguments": {"path": "x"}})
    assert result.get("isError") is True

    server._search_first_mode = "warn"
    result = server.handle_tools_call({"name": "read_symbol", "arguments": {"path": "x", "name": "y"}})
    assert "warnings" in result

    result = server.handle_tools_call({"name": "search", "arguments": {"query": "x"}})
    assert result.get("ok") is True

    result = server.handle_tools_call({"name": "search_symbols", "arguments": {"query": "x"}})
    assert result.get("ok") is True

    with pytest.raises(ValueError):
        server.handle_tools_call({"name": "unknown", "arguments": {}})


def test_shutdown(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.db = DummyDB()
    server.indexer = DummyIndexer()
    server.shutdown()
    assert server.db.closed is True
    assert server.indexer.stopped is True


def test_ensure_initialized(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()

    class DummyCfg:
        db_path = str(tmp_path / "db.sqlite")

    class DummyIndex:
        def __init__(self, cfg, db, logger):
            self.status = types.SimpleNamespace(index_ready=True)
        def run_forever(self):
            return None

    class DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
        def start(self):
            return None

    monkeypatch.setenv("DECKARD_INIT_TIMEOUT", "0")
    monkeypatch.setattr(server_mod, "Config", types.SimpleNamespace(load=lambda *args, **kwargs: DummyCfg()))
    monkeypatch.setattr(server_mod, "LocalSearchDB", lambda *args, **kwargs: DummyDB())
    monkeypatch.setattr(server_mod, "Indexer", DummyIndex)
    monkeypatch.setattr(server_mod.threading, "Thread", DummyThread)

    server._ensure_initialized()
    assert server.db is not None
    assert server.indexer is not None


def test_ensure_initialized_waits(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()

    class DummyCfg:
        db_path = str(tmp_path / "db.sqlite")

    class DummyIndex:
        def __init__(self, cfg, db, logger):
            self.status = types.SimpleNamespace(index_ready=True)
        def run_forever(self):
            return None

    class DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
        def start(self):
            return None

    monkeypatch.setenv("DECKARD_INIT_TIMEOUT", "0.1")
    monkeypatch.setattr(server_mod, "Config", types.SimpleNamespace(load=lambda *args, **kwargs: DummyCfg()))
    monkeypatch.setattr(server_mod, "LocalSearchDB", lambda *args, **kwargs: DummyDB())
    monkeypatch.setattr(server_mod, "Indexer", DummyIndex)
    monkeypatch.setattr(server_mod.threading, "Thread", DummyThread)
    server._ensure_initialized()


def test_search_first_enforce_read_symbol(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True
    server.db = DummyDB()
    server.indexer = DummyIndexer()
    server._search_first_mode = "enforce"
    result = server._tool_read_symbol({"path": "x", "name": "y"})
    assert result.get("isError") is True


def test_ensure_initialized_double_check(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = False

    class DummyLock:
        def __enter__(self):
            server._initialized = True
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    server._init_lock = DummyLock()
    server._ensure_initialized()


def test_ensure_initialized_error(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    monkeypatch.setattr(server_mod, "Config", types.SimpleNamespace(load=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad"))))
    with pytest.raises(RuntimeError):
        server._ensure_initialized()


def test_handle_request_error(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server._initialized = True

    def boom(_params):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "handle_tools_call", lambda _params: boom(_params))
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
    assert resp["error"]["code"] == -32000


def test_run_text_and_binary(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.indexer = DummyIndexer()
    server.db = DummyDB()
    server._initialized = True

    # text jsonl
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(server_mod.sys, "stdin", stdin)
    monkeypatch.setattr(server_mod.sys, "stdout", stdout)
    server.run()
    assert "jsonrpc" in stdout.getvalue()

    # binary framed with bad JSON
    bad_body = b"{bad}"
    payload = b"Content-Length: " + str(len(bad_body)).encode("ascii") + b"\r\n\r\n" + bad_body
    class BinIO:
        def __init__(self, data):
            self.buffer = io.BytesIO(data)
        def readline(self):
            return self.buffer.readline()
        def read(self, n=-1):
            return self.buffer.read(n)
    bin_stdin = BinIO(payload)
    class BinOut:
        def __init__(self):
            self.buffer = io.BytesIO()
    bin_stdout = BinOut()
    monkeypatch.setattr(server_mod.sys, "stdin", bin_stdin)
    monkeypatch.setattr(server_mod.sys, "stdout", bin_stdout)
    server.run()
    assert b"Parse error" in bin_stdout.buffer.getvalue()


def test_run_text_framed(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.indexer = DummyIndexer()
    server.db = DummyDB()
    server._initialized = True

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    payload = "\n\nContent-Length: " + str(len(body)) + "\r\n\r\n" + body
    stdin = io.StringIO(payload)
    stdout = io.StringIO()
    monkeypatch.setattr(server_mod.sys, "stdin", stdin)
    monkeypatch.setattr(server_mod.sys, "stdout", stdout)
    monkeypatch.setattr(server, "handle_request", lambda req: {"jsonrpc": "2.0", "id": req.get("id"), "result": {}})
    server.run()
    assert "Content-Length" in stdout.getvalue()


def test_run_binary_jsonl_and_invalid_content_length(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.indexer = DummyIndexer()
    server.db = DummyDB()
    server._initialized = True

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
    payload = b"\n\n" + body + b"\n"
    bin_stdin = types.SimpleNamespace(buffer=io.BytesIO(payload))
    bin_stdout = types.SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setattr(server_mod.sys, "stdin", bin_stdin)
    monkeypatch.setattr(server_mod.sys, "stdout", bin_stdout)
    monkeypatch.setattr(server, "handle_request", lambda req: {"jsonrpc": "2.0", "id": req.get("id"), "result": {}})
    server.run()
    assert b"jsonrpc" in bin_stdout.buffer.getvalue()

    bad_header = b"Content-Length: nope\r\n\r\n"
    bin_stdin = types.SimpleNamespace(buffer=io.BytesIO(bad_header))
    bin_stdout = types.SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setattr(server_mod.sys, "stdin", bin_stdin)
    monkeypatch.setattr(server_mod.sys, "stdout", bin_stdout)
    server.run()


def test_run_keyboard_interrupt(monkeypatch, tmp_path):
    server = server_mod.LocalSearchMCPServer(str(tmp_path))
    server.logger = DummyLogger()
    server.indexer = DummyIndexer()
    server.db = DummyDB()
    server._initialized = True

    class BadStdin:
        def readline(self):
            raise KeyboardInterrupt()

    monkeypatch.setattr(server_mod.sys, "stdin", BadStdin())
    monkeypatch.setattr(server_mod.sys, "stdout", io.StringIO())
    server.run()
    assert server.indexer.stopped is True
    assert server.db.closed is True


def test_main_entry(monkeypatch):
    monkeypatch.setattr(server_mod.WorkspaceManager, "resolve_workspace_root", lambda: "/tmp")
    monkeypatch.setattr(server_mod.LocalSearchMCPServer, "run", lambda self: None)
    server_mod.main()


def test_main_module_entry(monkeypatch):
    import sys
    sys.modules.pop("mcp.server", None)
    monkeypatch.setattr(server_mod.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(server_mod.LocalSearchMCPServer, "run", lambda self: None)
    runpy.run_module("mcp.server", run_name="__main__")
