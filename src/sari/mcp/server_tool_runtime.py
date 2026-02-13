"""Tool-call runtime resolution helpers for MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ToolRuntime:
    db: object
    indexer: object
    roots: list[str]
    session: object | None
    session_acquired: bool


def ensure_connection_id(args: dict[str, object], connection_id: str) -> dict[str, object]:
    out = dict(args)
    out["connection_id"] = connection_id
    return out


def resolve_tool_runtime(
    *,
    injected_cfg: object,
    injected_db: object,
    injected_indexer: object,
    session: object | None,
    registry: object,
    workspace_root: str,
    error_builder: Optional[Callable[[str], Exception]] = None,
) -> ToolRuntime:
    if injected_db is not None and injected_indexer is not None:
        roots = list(getattr(injected_cfg, "workspace_roots", []) or [workspace_root])
        return ToolRuntime(
            db=injected_db,
            indexer=injected_indexer,
            roots=roots,
            session=session,
            session_acquired=False,
        )

    next_session = session
    acquired = False
    if next_session is None:
        next_session = registry.get_or_create(workspace_root)
        acquired = True

    db = getattr(next_session, "db", None)
    indexer = getattr(next_session, "indexer", None)
    cfg_data = getattr(next_session, "config_data", {}) or {}
    roots = list(cfg_data.get("workspace_roots", [workspace_root]))
    if db is None:
        msg = "tools/call failed: session.db is unavailable"
        if error_builder is not None:
            raise error_builder(msg)
        raise RuntimeError(msg)

    return ToolRuntime(
        db=db,
        indexer=indexer,
        roots=roots,
        session=next_session,
        session_acquired=acquired,
    )
