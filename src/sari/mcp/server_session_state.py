"""Session state helpers for MCP server."""

from __future__ import annotations


def ensure_initialized_session(
    *,
    session: object,
    injected_db: object,
    registry: object,
    workspace_root: str,
    session_acquired: bool,
) -> tuple[object, bool]:
    if session is None and injected_db is None:
        return registry.get_or_create(workspace_root), True
    return session, session_acquired
