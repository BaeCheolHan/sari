import importlib
from typing import Any


def get_workspace_registry() -> Any:
    """
    Resolve workspace shared-state registry lazily.

    The concrete implementation currently lives under MCP runtime.
    This shim keeps core HTTP layer free of direct compile-time MCP imports.
    """
    module = importlib.import_module("sari.core.workspace_registry")
    try:
        registry_cls = module.Registry
    except AttributeError as exc:
        raise RuntimeError("Registry class is not available in sari.core.workspace_registry") from exc
    return registry_cls.get_instance()
