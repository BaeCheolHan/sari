"""Entrypoint orchestration for MCP server process."""

from __future__ import annotations

from typing import Callable


def run_entrypoint(
    *,
    original_stdout: object,
    resolve_workspace_root: Callable[[], str],
    server_factory: Callable[[str], object],
    stdout_obj: object,
    stderr_obj: object,
    set_stdout: Callable[[object], None],
) -> None:
    mcp_out = original_stdout or getattr(stdout_obj, "buffer", stdout_obj)
    set_stdout(stderr_obj)
    server = server_factory(resolve_workspace_root())
    server.run(mcp_out)
