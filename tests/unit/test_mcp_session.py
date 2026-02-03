import asyncio
import json
from types import SimpleNamespace

import pytest

import mcp.session as session_mod


class DummyWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def get_extra_info(self, _name):
        return ("127.0.0.1", 1234)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class DummyServer:
    def __init__(self):
        self.calls = []

    def handle_initialized(self, params):
        self.calls.append(("initialized", params))

    def handle_initialize(self, params):
        return {"ok": True}

    def handle_request(self, request):
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"ok": True}}


class DummyRegistry:
    def __init__(self):
        self.released = []
        self.shared = SimpleNamespace(server=DummyServer())

    def get_or_create(self, _root):
        return self.shared

    def release(self, root):
        self.released.append(root)

    @staticmethod
    def get_instance():
        return DummyRegistry()


def test_handle_connection_jsonl_rejected(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        reader.feed_data(b"{\"jsonrpc\":\"2.0\"}\n")
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Parse error" in writer.data or b"JSONL not supported" in writer.data

    asyncio.run(run())


def test_handle_connection_invalid_header(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        reader.feed_data(b"Bad-Header\r\n\r\n")
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Invalid protocol framing" in writer.data

    asyncio.run(run())


def test_handle_connection_malformed_header(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        reader.feed_data(b"Content-Length: 2\r\nBadHeader\r\n\r\n{}\r\n")
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Invalid protocol framing" in writer.data

    asyncio.run(run())


def test_process_requests(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)
    monkeypatch.setattr(session_mod.WorkspaceManager, "resolve_workspace_root", lambda: "/tmp")

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        await sess.process_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": "/tmp"}})
        assert b"Content-Length" in writer.data
        await sess.process_request({"jsonrpc": "2.0", "id": 2, "method": "initialized", "params": {}})
        await sess.process_request({"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}})
        await sess.process_request({"jsonrpc": "2.0", "id": 4, "method": "exit", "params": {}})

    asyncio.run(run())


def test_process_request_without_init(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        await sess.process_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
        assert b"Server not initialized" in writer.data

    asyncio.run(run())


def test_handle_connection_invalid_content_length(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        reader.feed_data(b"Content-Length: nope\r\n\r\n")
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Invalid Content-Length" in writer.data

    asyncio.run(run())


def test_handle_connection_missing_content_length(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        reader.feed_data(b"Content-Length: 0\r\n\r\n")
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Content-Length header required" in writer.data

    asyncio.run(run())


def test_handle_initialize_failure(monkeypatch):
    class BadRegistry(DummyRegistry):
        def get_or_create(self, _root):
            class S:
                def __init__(self):
                    self.server = DummyServer()
                    def bad_init(_):
                        raise RuntimeError("boom")
                    self.server.handle_initialize = bad_init
            return S()

        @staticmethod
        def get_instance():
            return BadRegistry()

    monkeypatch.setattr(session_mod, "Registry", BadRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        await sess.handle_initialize({"id": 1, "params": {"rootUri": "/tmp"}})
        assert b"boom" in writer.data

    asyncio.run(run())


def test_handle_connection_json_decode_error_with_id(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        body = b"{\"id\": 5,}"
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        reader.feed_data(header + body)
        reader.feed_eof()
        await sess.handle_connection()
        assert b"\"id\": 5" in writer.data
        assert b"Parse error" in writer.data

    asyncio.run(run())


def test_handle_connection_json_decode_error_bad_id(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        body = b'{"id":"bad\\u"}'
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        reader.feed_data(header + body)
        reader.feed_eof()
        await sess.handle_connection()
        assert b"Parse error" in writer.data

    asyncio.run(run())


def test_handle_connection_forward_error(monkeypatch):
    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        calls = {"n": 0}
        orig_json_loads = session_mod.json.loads
        def flaky_loads(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return orig_json_loads(payload)
            raise ValueError("boom")
        monkeypatch.setattr(
            session_mod,
            "json",
            SimpleNamespace(
                loads=flaky_loads,
                dumps=session_mod.json.dumps,
                JSONDecodeError=session_mod.json.JSONDecodeError,
            ),
        )

        sess.process_request = lambda _req: (_ for _ in ()).throw(RuntimeError("boom"))
        body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {}}).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        reader.feed_data(header + body)
        reader.feed_eof()
        await sess.handle_connection()
        assert b"boom" in writer.data

    asyncio.run(run())


def test_handle_initialize_file_uri_and_cleanup(monkeypatch):
    class LocalRegistry(DummyRegistry):
        @staticmethod
        def get_instance():
            return LocalRegistry()

    monkeypatch.setattr(session_mod, "Registry", LocalRegistry)
    monkeypatch.setattr(session_mod.WorkspaceManager, "resolve_workspace_root", lambda: "/tmp")

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        await sess.handle_initialize({"id": 1, "params": {"rootUri": "file:///tmp"}})
        sess.workspace_root = "/tmp"
        sess.cleanup()
        assert sess.workspace_root is None

    asyncio.run(run())


def test_handle_connection_body_empty(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    class EmptyReader:
        def __init__(self):
            self.calls = 0
        async def readline(self):
            self.calls += 1
            if self.calls == 1:
                return b"Content-Length: 1\r\n"
            return b"\r\n"
        async def readexactly(self, _n):
            return b""

    async def run():
        reader = EmptyReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        await sess.handle_connection()
        assert writer.data == bytearray()

    asyncio.run(run())


def test_handle_connection_connection_reset(monkeypatch):
    monkeypatch.setattr(session_mod, "Registry", DummyRegistry)

    class ResetReader:
        async def readline(self):
            raise ConnectionResetError()

    class BadWriter(DummyWriter):
        def close(self):
            async def boom():
                raise RuntimeError("close")
            return boom()
        async def wait_closed(self):
            raise RuntimeError("wait")

    async def run():
        reader = ResetReader()
        writer = BadWriter()
        sess = session_mod.Session(reader, writer)
        await sess.handle_connection()
        assert writer.closed is False

    asyncio.run(run())


def test_process_request_forward_and_send(monkeypatch):
    class LocalRegistry(DummyRegistry):
        @staticmethod
        def get_instance():
            return LocalRegistry()

    monkeypatch.setattr(session_mod, "Registry", LocalRegistry)
    monkeypatch.setattr(session_mod.WorkspaceManager, "resolve_workspace_root", lambda: "/tmp")

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        sess.shared_state = SimpleNamespace(server=DummyServer())
        await sess.process_request({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}})
        assert b"Content-Length" in writer.data

    asyncio.run(run())


def test_handle_initialize_fallback_root_and_release(monkeypatch):
    class LocalRegistry(DummyRegistry):
        @staticmethod
        def get_instance():
            return LocalRegistry()

    monkeypatch.setattr(session_mod, "Registry", LocalRegistry)
    monkeypatch.setattr(session_mod.WorkspaceManager, "resolve_workspace_root", lambda: "/tmp/fallback")

    async def run():
        reader = asyncio.StreamReader()
        writer = DummyWriter()
        sess = session_mod.Session(reader, writer)
        sess.workspace_root = "/old"
        sess.shared_state = SimpleNamespace(server=DummyServer())
        await sess.handle_initialize({"id": 1, "params": {}})
        assert sess.workspace_root == "/tmp/fallback"

    asyncio.run(run())


def test_send_json_awaitable_write(monkeypatch):
    class AwaitWriter(DummyWriter):
        def write(self, data):
            self.data.extend(data)
            async def done():
                return None
            return done()

    async def run():
        reader = asyncio.StreamReader()
        writer = AwaitWriter()
        sess = session_mod.Session(reader, writer)
        await sess.send_json({"ok": True})
        assert b"Content-Length" in writer.data

    asyncio.run(run())
