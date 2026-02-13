from __future__ import annotations

import io

from sari.mcp.server_entrypoint import run_entrypoint


def test_run_entrypoint_uses_original_stdout_or_sys_stdout_buffer(monkeypatch):
    class _Stdout:
        def __init__(self, buffer_obj):
            self.buffer = buffer_obj

    fake_sys_out = io.BytesIO()
    fake_sys_err = io.StringIO()
    monkeypatch.setattr("sys.stdout", _Stdout(fake_sys_out))
    monkeypatch.setattr("sys.stderr", fake_sys_err)

    seen = {}

    class _Server:
        def run(self, out):
            seen["out"] = out

    run_entrypoint(
        original_stdout=None,
        resolve_workspace_root=lambda: "/tmp/ws",
        server_factory=lambda _ws: _Server(),
        stdout_obj=__import__("sys").stdout,
        stderr_obj=__import__("sys").stderr,
        set_stdout=lambda v: setattr(__import__("sys"), "stdout", v),
    )

    assert seen["out"] is fake_sys_out
    assert __import__("sys").stdout is fake_sys_err


def test_run_entrypoint_prefers_explicit_original_stdout(monkeypatch):
    fake_sys_out = io.BytesIO()
    fake_sys_err = io.StringIO()
    explicit = io.BytesIO()

    class _Stdout:
        def __init__(self, buffer_obj):
            self.buffer = buffer_obj

    monkeypatch.setattr("sys.stdout", _Stdout(fake_sys_out))
    monkeypatch.setattr("sys.stderr", fake_sys_err)

    seen = {}

    class _Server:
        def run(self, out):
            seen["out"] = out

    run_entrypoint(
        original_stdout=explicit,
        resolve_workspace_root=lambda: "/tmp/ws",
        server_factory=lambda _ws: _Server(),
        stdout_obj=__import__("sys").stdout,
        stderr_obj=__import__("sys").stderr,
        set_stdout=lambda v: setattr(__import__("sys"), "stdout", v),
    )
    assert seen["out"] is explicit
