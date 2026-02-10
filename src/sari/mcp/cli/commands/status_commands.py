"""
Status/search command handlers extracted from legacy_cli.
"""

import json
import os
import time
from typing import Any, Optional, Tuple

from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.daemon_resolver import resolve_daemon_address as get_daemon_address

from ..daemon import is_daemon_running
from ..http_client import get_http_host_port as _get_http_host_port
from ..http_client import is_http_running as _is_http_running
from ..http_client import request_http as _request_http
from ..mcp_client import ensure_workspace_http as _ensure_workspace_http
from ..mcp_client import request_mcp_status as _request_mcp_status
from ..smart_daemon import ensure_smart_daemon


def _arg(args: Any, name: str, default: Any = None) -> Any:
    return getattr(args, name, default) if hasattr(args, name) else default


def _ensure_daemon_running(h, p, **kwargs):
    res_h, res_p = ensure_smart_daemon(h, p)
    return res_h, res_p, True


def _resolve_http_endpoint_for_daemon(args: Any, daemon_host: str, daemon_port: int) -> Tuple[str, int]:
    host_override = _arg(args, "http_host")
    port_override = _arg(args, "http_port")
    if host_override or port_override is not None:
        return _get_http_host_port(host_override, port_override)

    host, port = _get_http_host_port(None, None)
    try:
        reg = ServerRegistry()
        inst = reg.resolve_daemon_by_endpoint(daemon_host, daemon_port)
        if not inst:
            ws_root = os.environ.get("SARI_WORKSPACE_ROOT") or WorkspaceManager.resolve_workspace_root()
            inst = reg.resolve_workspace_daemon(str(ws_root))
        if inst:
            if inst.get("http_host"):
                host = str(inst.get("http_host"))
            if inst.get("http_port"):
                port = int(inst.get("http_port"))
    except Exception:
        pass
    return host, port


def cmd_status(args):
    try:
        if _arg(args, "daemon_host") or _arg(args, "daemon_port"):
            d_host = _arg(args, "daemon_host") or DEFAULT_DAEMON_HOST
            d_port = int(_arg(args, "daemon_port") or DEFAULT_DAEMON_PORT)
        else:
            d_host, d_port = get_daemon_address()
        daemon_running = is_daemon_running(d_host, d_port)

        h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
        http_running = _is_http_running(h, p)

        if not http_running:
            if not daemon_running:
                d_host, d_port, daemon_running = _ensure_daemon_running(d_host, d_port, allow_upgrade=False)
                h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
            if daemon_running:
                for _ in range(5):
                    _ensure_workspace_http(d_host, d_port)
                    h, p = _resolve_http_endpoint_for_daemon(args, d_host, d_port)
                    http_running = _is_http_running(h, p)
                    if http_running:
                        break
                    time.sleep(0.1)

            if not http_running and daemon_running:
                fallback = _request_mcp_status(d_host, d_port)
                if fallback:
                    print(json.dumps(fallback, ensure_ascii=False, indent=2))
                    return 0

            if not http_running:
                print(f"❌ Error: Sari services not running. Daemon: {d_host}:{d_port}, HTTP: {h}:{p}")
                return 1

        data = _request_http("/status", {}, h, p)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


def cmd_search(args):
    params = {"q": args.query, "limit": args.limit}
    if _arg(args, "repo"):
        params["repo"] = args.repo
    data = _request_http("/search", params)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
