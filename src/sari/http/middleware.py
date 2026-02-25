"""HTTP middleware implementations."""

from __future__ import annotations

import http.client
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sari.http.context import HttpContext
from sari.http.endpoint_resolver import resolve_http_endpoint
from sari.http.ports import RuntimeRepoPort


class RuntimeSessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, runtime_repo: RuntimeRepoPort) -> None:
        super().__init__(app)
        self._runtime_repo = runtime_repo

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return await call_next(request)
        self._runtime_repo.increment_session()
        try:
            return await call_next(request)
        finally:
            self._runtime_repo.decrement_session()


class BackgroundProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        context: HttpContext = request.app.state.context
        if not context.http_bg_proxy_enabled:
            return await call_next(request)
        target = _parse_proxy_target(context.http_bg_proxy_target.strip())
        if target is None:
            if context.db_path is None:
                return JSONResponse(
                    {"error": {"code": "ERR_HTTP_ENDPOINT_UNRESOLVED", "message": "background proxy endpoint cannot be resolved"}},
                    status_code=503,
                )
            resolved = resolve_http_endpoint(db_path=context.db_path, workspace_root=None)
            target = (resolved.host, resolved.port)
        request_port = request.url.port
        request_host = request.url.hostname
        if request_host == target[0] and request_port == target[1]:
            return await call_next(request)
        if request.url.path == "/health":
            return await call_next(request)
        request_body = await request.body()
        try:
            return _forward_upstream_request(host=target[0], port=target[1], request=request, request_body=request_body)
        except (OSError, TimeoutError, ValueError) as exc:
            return JSONResponse({"error": {"code": "ERR_HTTP_PROXY_FAILED", "message": f"background proxy failed: {exc}"}}, status_code=502)


def _parse_proxy_target(raw_target: str) -> tuple[str, int] | None:
    if raw_target == "":
        return None
    if ":" not in raw_target:
        raise ValueError("SARI_HTTP_BG_PROXY_TARGET must be host:port")
    host, raw_port = raw_target.split(":", 1)
    host_value = host.strip()
    if host_value == "":
        raise ValueError("proxy host is empty")
    try:
        port_value = int(raw_port.strip())
    except ValueError as exc:
        raise ValueError("proxy port must be integer") from exc
    if port_value <= 0:
        raise ValueError("proxy port must be positive")
    return (host_value, port_value)


def _forward_upstream_request(host: str, port: int, request: Request, request_body: bytes) -> Response:
    query_string = request.url.query
    path = request.url.path
    if query_string != "":
        path = f"{path}?{query_string}"
    filtered_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {"host", "connection", "content-length"}:
            continue
        filtered_headers[key] = value
    connection = http.client.HTTPConnection(host, port, timeout=2.0)
    try:
        connection.request(request.method, path, body=request_body, headers=filtered_headers)
        upstream_response = connection.getresponse()
        response_body = upstream_response.read()
        response_headers: dict[str, str] = {}
        for key, value in upstream_response.getheaders():
            lowered = key.lower()
            if lowered in {"transfer-encoding", "connection", "content-length"}:
                continue
            response_headers[key] = value
        return Response(content=response_body, status_code=upstream_response.status, headers=response_headers)
    finally:
        connection.close()
