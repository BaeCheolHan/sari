"""Daemon endpoint helpers used by doctor/status tooling."""

import os
from typing import Optional

from sari.mcp.server_registry import ServerRegistry
from sari.mcp.cli.mcp_client import identify_sari_daemon


def read_pid(host: str, port: int) -> Optional[int]:
    try:
        # Prefer direct module import to avoid pulling full CLI package graph.
        from sari.mcp.cli.daemon import read_pid as cli_read_pid

        pid = cli_read_pid(host, port)
        if pid:
            return int(pid)
    except Exception:
        try:
            # Backward-compat fallback for tests patching sari.mcp.cli.read_pid.
            from sari.mcp.cli import read_pid as cli_read_pid

            pid = cli_read_pid(host, port)
            if pid:
                return int(pid)
        except Exception:
            pass
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(host, port)
        return int(inst["pid"]) if inst and inst.get("pid") else None
    except Exception:
        return None


def get_http_host_port(port_override: Optional[int] = None) -> tuple[str, int]:
    from sari.core.constants import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT

    host = os.environ.get("SARI_HTTP_HOST") or DEFAULT_HTTP_HOST
    port = port_override or int(os.environ.get("SARI_HTTP_PORT") or DEFAULT_HTTP_PORT)
    return host, port


def resolve_http_endpoint_for_daemon(
    daemon_host: str, daemon_port: int, port_override: Optional[int] = None
) -> tuple[str, int]:
    host, port = get_http_host_port(port_override=port_override)
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(daemon_host, daemon_port)
        if inst:
            if inst.get("http_host"):
                host = str(inst.get("http_host"))
            if inst.get("http_port"):
                port = int(inst.get("http_port"))
    except Exception:
        pass
    return host, port


def identify(host: str, port: int):
    return identify_sari_daemon(host, port)
