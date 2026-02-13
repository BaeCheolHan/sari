"""Initialize/protocol negotiation helpers for MCP server."""

from __future__ import annotations

from typing import Callable, Mapping, Optional


def choose_target_uri(params: Mapping[str, object]) -> Optional[str]:
    root_uri = params.get("rootUri") or params.get("rootPath")
    if isinstance(root_uri, str) and root_uri:
        return root_uri

    workspace_folders = params.get("workspaceFolders", [])
    if isinstance(workspace_folders, list) and workspace_folders:
        first = workspace_folders[0]
        if isinstance(first, dict):
            uri = first.get("uri")
            if isinstance(uri, str) and uri:
                return uri
    return None


def iter_client_protocol_versions(params: Mapping[str, object]) -> list[str]:
    versions: list[str] = []
    seen = set()

    def _append(v: object) -> None:
        if not isinstance(v, str):
            return
        vv = v.strip()
        if not vv or vv in seen:
            return
        seen.add(vv)
        versions.append(vv)

    _append(params.get("protocolVersion"))
    for v in (params.get("supportedProtocolVersions") or []):
        _append(v)
    caps = params.get("capabilities")
    if isinstance(caps, dict):
        for v in (caps.get("protocolVersions") or []):
            _append(v)
    return versions


def negotiate_protocol_version(
    params: Mapping[str, object],
    supported_versions: set[str],
    default_version: str,
    strict_protocol: bool,
    error_builder: Optional[Callable[[list[str]], Exception]] = None,
) -> str:
    client_versions = iter_client_protocol_versions(params)
    for v in client_versions:
        if v in supported_versions:
            return v

    if strict_protocol and client_versions:
        if error_builder is not None:
            raise error_builder(sorted(list(supported_versions)))
        raise ValueError("Unsupported protocol version")

    return default_version


def build_initialize_result(
    protocol_version: str,
    server_name: str,
    server_version: str,
) -> dict[str, object]:
    return {
        "protocolVersion": protocol_version,
        "serverInfo": {"name": server_name, "version": server_version},
        "capabilities": {
            "tools": {"listChanged": False},
            "prompts": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "roots": {"listChanged": False},
        },
    }
