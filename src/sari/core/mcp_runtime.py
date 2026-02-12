import importlib
from typing import Any


def create_mcp_server(
    workspace_root: str,
    cfg: object = None,
    db: object = None,
    indexer: object = None,
) -> Any:
    """
    Build MCP server instance lazily.
    """
    module = importlib.import_module("sari.mcp.server")
    server_cls = getattr(module, "LocalSearchMCPServer")
    if cfg is not None:
        return server_cls(workspace_root, cfg=cfg, db=db, indexer=indexer)
    return server_cls(workspace_root)
