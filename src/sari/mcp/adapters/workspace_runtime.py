from __future__ import annotations

from typing import Protocol, runtime_checkable

from sari.mcp.workspace_registry import Registry


@runtime_checkable
class WorkspaceRuntime(Protocol):
    def get_or_create(
        self,
        workspace_root: str,
        persistent: bool = False,
        track_ref: bool = True,
    ) -> object:
        ...

    def touch_workspace(self, workspace_root: str) -> None:
        ...

    def release(self, workspace_root: str) -> None:
        ...


class RegistryWorkspaceRuntime:
    """Adapter over Registry singleton used by MCP runtime paths."""

    def __init__(self, registry: Registry | None = None):
        self._registry = registry or Registry.get_instance()

    def get_or_create(
        self,
        workspace_root: str,
        persistent: bool = False,
        track_ref: bool = True,
    ) -> object:
        return self._registry.get_or_create(
            workspace_root,
            persistent=persistent,
            track_ref=track_ref,
        )

    def touch_workspace(self, workspace_root: str) -> None:
        self._registry.touch_workspace(workspace_root)

    def release(self, workspace_root: str) -> None:
        self._registry.release(workspace_root)


def get_workspace_runtime() -> WorkspaceRuntime:
    return RegistryWorkspaceRuntime()
