import importlib
from typing import Any


def get_workspace_registry() -> Any:
    """
    Resolve workspace shared-state registry lazily.

    The concrete implementation currently lives under MCP runtime.
    This shim keeps core HTTP layer free of direct compile-time MCP imports.
    """
    module = importlib.import_module("sari.mcp.workspace_registry")
    registry_cls = getattr(module, "Registry")
    return registry_cls.get_instance()
