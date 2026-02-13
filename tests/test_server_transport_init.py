from __future__ import annotations

import io

from sari.mcp.server_transport_init import ensure_transport


class _DummyTransport:
    def __init__(self):
        self.default_mode = "content-length"


def test_ensure_transport_creates_with_output_priority():
    inp = io.BytesIO()
    sys_out = io.BytesIO()
    original_out = io.BytesIO()
    explicit_out = io.BytesIO()
    seen = {}

    class _Std:
        def __init__(self, buffer_obj):
            self.buffer = buffer_obj

    def _factory(input_stream, output_stream, allow_jsonl=False):
        seen["input_stream"] = input_stream
        seen["output_stream"] = output_stream
        seen["allow_jsonl"] = allow_jsonl
        return _DummyTransport()

    transport = ensure_transport(
        transport=None,
        output_stream=explicit_out,
        original_stdout=original_out,
        stdin_obj=_Std(inp),
        stdout_obj=_Std(sys_out),
        env={"SARI_FORMAT": "pack"},
        transport_factory=_factory,
    )
    assert transport.default_mode == "content-length"
    assert seen["input_stream"] is inp
    assert seen["output_stream"] is explicit_out
    assert seen["allow_jsonl"] is True


def test_ensure_transport_uses_json_mode_and_existing_transport_passthrough():
    sys_in = io.BytesIO()
    sys_out = io.BytesIO()

    class _Std:
        def __init__(self, buffer_obj):
            self.buffer = buffer_obj

    created = ensure_transport(
        transport=None,
        output_stream=None,
        original_stdout=None,
        stdin_obj=_Std(sys_in),
        stdout_obj=_Std(sys_out),
        env={"SARI_FORMAT": " json "},
        transport_factory=lambda *_a, **_k: _DummyTransport(),
    )
    assert created.default_mode == "jsonl"

    existing = _DummyTransport()
    reused = ensure_transport(
        transport=existing,
        output_stream=None,
        original_stdout=None,
        stdin_obj=_Std(sys_in),
        stdout_obj=_Std(sys_out),
        env={"SARI_FORMAT": "json"},
        transport_factory=lambda *_a, **_k: _DummyTransport(),
    )
    assert reused is existing
