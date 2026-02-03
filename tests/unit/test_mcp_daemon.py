import asyncio
import os
from pathlib import Path

import pytest

import mcp.daemon as daemon_mod


class DummySession:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.handled = False

    async def handle_connection(self):
        self.handled = True


class DummyWriter:
    def get_extra_info(self, _name):
        return ("127.0.0.1", 1234)


def test_resolve_log_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKARD_LOG_DIR", str(tmp_path))
    assert daemon_mod._resolve_log_dir() == tmp_path


def test_init_logging_fallback(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_file_handler(_path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fail")
        class H:
            pass
        return H()

    monkeypatch.setenv("DECKARD_LOG_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(daemon_mod.logging, "FileHandler", fake_file_handler)
    daemon_mod._init_logging()


def test_init_logging_double_fallback_failure(monkeypatch, tmp_path):
    def fake_file_handler(_path):
        raise RuntimeError("fail")

    monkeypatch.setenv("DECKARD_LOG_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(daemon_mod.logging, "FileHandler", fake_file_handler)
    daemon_mod._init_logging()


def test_write_remove_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_mod, "PID_FILE", tmp_path / "daemon.pid")
    daemon = daemon_mod.DeckardDaemon()
    daemon._write_pid()
    assert daemon_mod.PID_FILE.exists()
    daemon._remove_pid()
    assert not daemon_mod.PID_FILE.exists()


def test_start_enforce_loopback(monkeypatch):
    daemon = daemon_mod.DeckardDaemon()
    daemon.host = "0.0.0.0"
    monkeypatch.delenv("DECKARD_ALLOW_NON_LOOPBACK", raising=False)
    monkeypatch.delenv("LOCAL_SEARCH_ALLOW_NON_LOOPBACK", raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(daemon.start())


def test_start_success(monkeypatch, tmp_path):
    daemon = daemon_mod.DeckardDaemon()
    daemon.host = "localhost"

    class DummyServer:
        sockets = [type("S", (), {"getsockname": lambda self: ("127.0.0.1", 1)})()]
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def serve_forever(self):
            return None

    monkeypatch.setattr(daemon_mod, "PID_FILE", tmp_path / "daemon.pid")
    async def fake_start_server(*args, **kwargs):
        return DummyServer()

    monkeypatch.setattr(daemon_mod.asyncio, "start_server", fake_start_server)
    asyncio.run(daemon.start())


def test_start_invalid_host_valueerror(monkeypatch, tmp_path):
    daemon = daemon_mod.DeckardDaemon()
    daemon.host = "not-an-ip"

    class DummyServer:
        sockets = [type("S", (), {"getsockname": lambda self: ("127.0.0.1", 1)})()]
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def serve_forever(self):
            return None

    async def fake_start_server(*_args, **_kwargs):
        return DummyServer()

    monkeypatch.setenv("DECKARD_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setattr(daemon_mod, "PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr(daemon_mod.asyncio, "start_server", fake_start_server)
    asyncio.run(daemon.start())


def test_handle_client(monkeypatch):
    daemon = daemon_mod.DeckardDaemon()
    monkeypatch.setattr(daemon_mod, "Session", DummySession)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        await daemon.handle_client(reader, writer)

    asyncio.run(run())


def test_shutdown(monkeypatch):
    class DummyServer:
        def __init__(self):
            self.closed = False
        def close(self):
            self.closed = True

    daemon = daemon_mod.DeckardDaemon()
    daemon.server = DummyServer()
    import mcp.registry as registry_mod

    class DummyRegistry:
        @staticmethod
        def get_instance():
            class R:
                def shutdown_all(self):
                    self.called = True
            return R()

    monkeypatch.setattr(registry_mod, "Registry", DummyRegistry)
    daemon.shutdown()
    assert daemon.server.closed is True


def test_pid_error_paths(monkeypatch):
    class BadPath:
        def __init__(self):
            self.parent = self
        def mkdir(self, *args, **kwargs):
            raise RuntimeError("mkdir")
        def write_text(self, *_args, **_kwargs):
            raise RuntimeError("write")
        def exists(self):
            return True
        def unlink(self):
            raise RuntimeError("unlink")

    daemon = daemon_mod.DeckardDaemon()
    monkeypatch.setattr(daemon_mod, "PID_FILE", BadPath())
    daemon._write_pid()
    daemon._remove_pid()


def test_main_flow(monkeypatch):
    async def dummy_start(self):
        return None

    monkeypatch.setattr(daemon_mod.DeckardDaemon, "start", dummy_start)

    called = {"n": 0}

    def fake_add_signal_handler(sig, handler):
        if called["n"] == 0:
            called["n"] += 1
            handler()

    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", fake_add_signal_handler)
        await daemon_mod.main()

    asyncio.run(run())


def test_main_module_entry(monkeypatch):
    import runpy
    import sys
    sys.modules.pop("mcp.daemon", None)

    def fake_run(coro):
        coro.close()
        return None
    monkeypatch.setattr(asyncio, "run", fake_run)
    runpy.run_module("mcp.daemon", run_name="__main__")
