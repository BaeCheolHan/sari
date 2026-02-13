"""Transport initialization helpers for MCP server run loop."""

from __future__ import annotations

from typing import Callable, Mapping


def ensure_transport(
    *,
    transport: object,
    output_stream: object,
    original_stdout: object,
    stdin_obj: object,
    stdout_obj: object,
    env: Mapping[str, str],
    transport_factory: Callable[[object, object, bool], object],
) -> object:
    if transport is not None:
        return transport

    input_stream = getattr(stdin_obj, "buffer", stdin_obj)
    target_out = output_stream or original_stdout or getattr(stdout_obj, "buffer", stdout_obj)
    wire_format = (env.get("SARI_FORMAT") or "pack").strip().lower()
    created = transport_factory(input_stream, target_out, allow_jsonl=True)
    if wire_format == "json":
        created.default_mode = "jsonl"
    else:
        created.default_mode = "content-length"
    return created
