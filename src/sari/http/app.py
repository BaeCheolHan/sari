import html
import http.client
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
from sari import __version__
from sari.core.exceptions import DaemonError, PerfError, QualityError, ValidationError
from sari.core.language_registry import get_enabled_language_names
from sari.core.models import ErrorResponseDTO, HealthResponseDTO, LanguageProbeStatusDTO
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.http.admin_endpoints import (
    daemon_reconcile_endpoint,
    daemon_list_endpoint,
    doctor_endpoint,
    errors_endpoint,
    repo_candidates_endpoint,
    rescan_endpoint,
)
from sari.http.context import HttpContext
from sari.http.pipeline_error_endpoints import (
    pipeline_error_detail_api_endpoint,
    pipeline_error_detail_html_endpoint,
    pipeline_errors_api_endpoint,
    pipeline_errors_html_endpoint,
)
from sari.http.request_parsers import (
    build_read_arguments,
    parse_fail_on_unavailable_from_query,
    parse_bool_value,
    parse_strict_all_languages_from_query,
    parse_strict_symbol_gate_from_query,
    read_language_filter_from_query,
    read_required_languages_from_query,
    resolve_format,
    resolve_repo_from_query,
    resolve_repo_from_value,
)
from sari.http.response_builders import read_response
from sari.http.endpoint_resolver import resolve_http_endpoint
class RuntimeSessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, runtime_repo: RuntimeRepository) -> None:
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
                    {'error': {'code': 'ERR_HTTP_ENDPOINT_UNRESOLVED', 'message': 'background proxy endpoint cannot be resolved'}},
                    status_code=503,
                )
            resolved = resolve_http_endpoint(db_path=context.db_path, workspace_root=None)
            target = (resolved.host, resolved.port)
        request_port = request.url.port
        request_host = request.url.hostname
        if request_host == target[0] and request_port == target[1]:
            return await call_next(request)
        if request.url.path == '/health':
            return await call_next(request)
        request_body = await request.body()
        try:
            return _forward_upstream_request(host=target[0], port=target[1], request=request, request_body=request_body)
        except (OSError, TimeoutError, ValueError) as exc:
            return JSONResponse({'error': {'code': 'ERR_HTTP_PROXY_FAILED', 'message': f'background proxy failed: {exc}'}}, status_code=502)
def _parse_proxy_target(raw_target: str) -> tuple[str, int] | None:
    if raw_target == '':
        return None
    if ':' not in raw_target:
        raise ValueError('SARI_HTTP_BG_PROXY_TARGET must be host:port')
    host, raw_port = raw_target.split(':', 1)
    host_value = host.strip()
    if host_value == '':
        raise ValueError('proxy host is empty')
    try:
        port_value = int(raw_port.strip())
    except ValueError as exc:
        raise ValueError('proxy port must be integer') from exc
    if port_value <= 0:
        raise ValueError('proxy port must be positive')
    return (host_value, port_value)
def _forward_upstream_request(host: str, port: int, request: Request, request_body: bytes) -> Response:
    query_string = request.url.query
    path = request.url.path
    if query_string != '':
        path = f'{path}?{query_string}'
    filtered_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {'host', 'connection', 'content-length'}:
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
            if lowered in {'transfer-encoding', 'connection', 'content-length'}:
                continue
            response_headers[key] = value
        return Response(content=response_body, status_code=upstream_response.status, headers=response_headers)
    finally:
        connection.close()
async def health_endpoint(request) -> JSONResponse:
    payload = HealthResponseDTO(status='ok', version=__version__, uptime_sec=0.0)
    return JSONResponse({'status': payload.status, 'version': payload.version, 'uptime_sec': payload.uptime_sec})
async def status_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    runtime = context.runtime_repo.get_runtime()
    workspaces = context.workspace_repo.list_all()
    language_support = _build_language_support_payload(context.language_probe_repo)
    lsp_metrics = context.lsp_metrics_provider() if context.lsp_metrics_provider is not None else {}
    reconcile_state = context.admin_service.get_runtime_reconcile_state()
    auto_control = None
    stage_rollout = None
    if context.pipeline_control_service is not None:
        auto_control = context.pipeline_control_service.get_auto_control_state().to_dict()
        stage_rollout = context.pipeline_control_service.get_stage_rollout_state()
    if runtime is None:
        metrics = None
        if context.file_collection_service is not None:
            metrics = context.file_collection_service.get_pipeline_metrics().to_dict()
        return JSONResponse({'daemon': None, 'workspace_count': len(workspaces), 'phase': 'phase2', 'run_mode': context.admin_service.run_mode(), 'pipeline_metrics': metrics, 'language_support': language_support, 'daemon_lifecycle': None, 'lsp_metrics': lsp_metrics, 'reconcile_state': reconcile_state, 'auto_control': auto_control, 'stage_rollout': stage_rollout})
    metrics = None
    if context.file_collection_service is not None:
        metrics = context.file_collection_service.get_pipeline_metrics().to_dict()
    return JSONResponse({'daemon': {'pid': runtime.pid, 'host': runtime.host, 'port': runtime.port, 'state': runtime.state, 'started_at': runtime.started_at, 'session_count': runtime.session_count, 'last_heartbeat_at': runtime.last_heartbeat_at, 'last_exit_reason': runtime.last_exit_reason}, 'workspace_count': len(workspaces), 'phase': 'phase2', 'run_mode': context.admin_service.run_mode(), 'pipeline_metrics': metrics, 'language_support': language_support, 'daemon_lifecycle': {'last_heartbeat_at': runtime.last_heartbeat_at, 'heartbeat_age_sec': _heartbeat_age_sec(runtime.last_heartbeat_at), 'last_exit_reason': runtime.last_exit_reason}, 'lsp_metrics': lsp_metrics, 'reconcile_state': reconcile_state, 'auto_control': auto_control, 'stage_rollout': stage_rollout})


async def mcp_jsonrpc_endpoint(request) -> JSONResponse:
    """데몬 내부 MCP JSON-RPC 요청을 HTTP 경유로 처리한다."""
    mcp_server = getattr(request.app.state, "mcp_server", None)
    if mcp_server is None:
        return JSONResponse({"error": {"code": "ERR_MCP_SERVER_UNAVAILABLE", "message": "mcp server is unavailable"}}, status_code=503)
    try:
        payload_raw = await request.json()
    except ValueError:
        return JSONResponse({"error": {"code": "ERR_INVALID_JSON_BODY", "message": "invalid json body"}}, status_code=400)
    if not isinstance(payload_raw, dict):
        return JSONResponse({"error": {"code": "ERR_INVALID_JSON_BODY", "message": "json body must be object"}}, status_code=400)
    response = mcp_server.handle_request(payload_raw)
    return JSONResponse(response.to_dict())
def _build_language_support_payload(probe_repo: LanguageProbeRepository | None) -> dict[str, object]:
    enabled_languages = list(get_enabled_language_names())
    snapshot_by_language: dict[str, LanguageProbeStatusDTO] = {}
    if probe_repo is not None:
        for item in probe_repo.list_all():
            snapshot_by_language[item.language] = item
    languages: list[dict[str, object]] = []
    for language in enabled_languages:
        snapshot = snapshot_by_language.get(language)
        if snapshot is None:
            languages.append({'language': language, 'enabled': True, 'available': False, 'last_probe_at': None, 'last_error_code': None, 'last_error_message': None, 'symbol_extract_success': False, 'document_symbol_count': 0, 'path_mapping_ok': False, 'timeout_occurred': False, 'recovered_by_restart': False})
            continue
        languages.append({'language': snapshot.language, 'enabled': snapshot.enabled, 'available': snapshot.available, 'last_probe_at': snapshot.last_probe_at, 'last_error_code': snapshot.last_error_code, 'last_error_message': snapshot.last_error_message, 'symbol_extract_success': snapshot.symbol_extract_success, 'document_symbol_count': snapshot.document_symbol_count, 'path_mapping_ok': snapshot.path_mapping_ok, 'timeout_occurred': snapshot.timeout_occurred, 'recovered_by_restart': snapshot.recovered_by_restart})
    available_count = len([item for item in languages if bool(item['available'])])
    return {'enabled': enabled_languages, 'enabled_count': len(enabled_languages), 'available_count': available_count, 'active_last_5m': [], 'languages': languages}
def _heartbeat_age_sec(last_heartbeat_at: str) -> float:
    try:
        parsed = datetime.fromisoformat(last_heartbeat_at)
    except ValueError:
        return -1.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
async def workspaces_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    items = context.workspace_repo.list_all()
    return JSONResponse({'items': [{'path': item.path, 'name': item.name, 'indexed_at': item.indexed_at, 'is_active': item.is_active} for item in items]})
async def read_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.read_facade_service is None:
        error = ErrorResponseDTO(code='ERR_HTTP_READ_UNAVAILABLE', message='read service is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    repo_id, repo, repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo_id is not None
    assert repo is not None
    mode_raw = str(request.query_params.get('mode', '')).strip().lower()
    if mode_raw == '':
        error = ErrorResponseDTO(code='ERR_MODE_REQUIRED', message='mode is required')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    output_format, format_error = resolve_format(request.query_params.get('format'))
    if format_error is not None:
        return format_error
    source = {str(k): v for k, v in request.query_params.items()}
    arguments, arg_error = build_read_arguments(repo_root=repo, repo_key=repo_key, mode=mode_raw, source=source)
    if arg_error is not None:
        return arg_error
    assert arguments is not None
    arguments["repo_id"] = repo_id
    payload = context.read_facade_service.read(arguments=arguments)
    return read_response(payload=payload, output_format=output_format)
async def read_file_endpoint(request) -> JSONResponse:
    query = dict(request.query_params)
    query['mode'] = 'file'
    proxy_request = type('ReadProxyRequest', (), {'query_params': query, 'app': request.app})()
    return await read_endpoint(proxy_request)
async def read_symbol_endpoint(request) -> JSONResponse:
    query = dict(request.query_params)
    query['mode'] = 'symbol'
    proxy_request = type('ReadProxyRequest', (), {'query_params': query, 'app': request.app})()
    return await read_endpoint(proxy_request)
async def read_snippet_endpoint(request) -> JSONResponse:
    query = dict(request.query_params)
    query['mode'] = 'snippet'
    proxy_request = type('ReadProxyRequest', (), {'query_params': query, 'app': request.app})()
    return await read_endpoint(proxy_request)
async def read_diff_preview_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.read_facade_service is None:
        error = ErrorResponseDTO(code='ERR_HTTP_READ_UNAVAILABLE', message='read service is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    try:
        body_raw = await request.json()
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_JSON_BODY', message='invalid json body')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    if not isinstance(body_raw, dict):
        error = ErrorResponseDTO(code='ERR_INVALID_JSON_BODY', message='json body must be object')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    repo_id, repo, repo_key, error_response = resolve_repo_from_value(context, body_raw.get('repo'))
    if error_response is not None:
        return error_response
    assert repo_id is not None
    assert repo is not None
    output_format, format_error = resolve_format(body_raw.get('format'))
    if format_error is not None:
        return format_error
    arguments, arg_error = build_read_arguments(repo_root=repo, repo_key=repo_key, mode='diff_preview', source=body_raw)
    if arg_error is not None:
        return arg_error
    assert arguments is not None
    arguments["repo_id"] = repo_id
    payload = context.read_facade_service.read(arguments=arguments)
    return read_response(payload=payload, output_format=output_format)
async def search_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo_id is not None
    assert repo is not None
    query = str(request.query_params.get('q', '')).strip()
    limit_raw = str(request.query_params.get('limit', '20'))
    if query == '':
        error = ErrorResponseDTO(code='ERR_QUERY_REQUIRED', message='q는 비어 있을 수 없습니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    try:
        limit = int(limit_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT', message='limit는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    if limit <= 0:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT', message='limit는 1 이상이어야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    resolve_symbols_default = False
    provider = getattr(context, "search_resolve_symbols_default_provider", None)
    if callable(provider):
        try:
            resolve_symbols_default = bool(provider())
        except (RuntimeError, OSError, ValueError, TypeError):
            resolve_symbols_default = False
    try:
        result = context.search_orchestrator.search(
            query=query,
            limit=limit,
            repo_root=repo,
            repo_id=repo_id,
            resolve_symbols=resolve_symbols_default,
        )
    except TypeError:
        result = context.search_orchestrator.search(query=query, limit=limit, repo_root=repo)
    progress_meta = _search_progress_meta(context)
    if result.meta.fatal_error:
        first_error = result.meta.errors[0]
        meta_payload: dict[str, object] = {'candidate_count': result.meta.candidate_count, 'resolved_count': result.meta.resolved_count, 'candidate_source': result.meta.candidate_source, 'fatal_error': result.meta.fatal_error, 'degraded': result.meta.degraded, 'error_count': result.meta.error_count, 'ranking_policy': result.meta.ranking_policy, 'rrf_k': result.meta.rrf_k, 'lsp_query_mode': result.meta.lsp_query_mode, 'lsp_sync_mode': result.meta.lsp_sync_mode, 'lsp_fallback_used': result.meta.lsp_fallback_used, 'lsp_fallback_reason': result.meta.lsp_fallback_reason, 'lsp_include_info_requested': result.meta.include_info_requested, 'lsp_symbol_info_budget_sec': result.meta.symbol_info_budget_sec, 'lsp_symbol_info_requested_count': result.meta.symbol_info_requested_count, 'lsp_symbol_info_budget_exceeded_count': result.meta.symbol_info_budget_exceeded_count, 'lsp_symbol_info_skipped_count': result.meta.symbol_info_skipped_count, 'importance_policy': result.meta.importance_policy, 'importance_weights': result.meta.importance_weights, 'importance_normalize_mode': result.meta.importance_normalize_mode, 'importance_max_boost': result.meta.importance_max_boost, 'vector_enabled': result.meta.vector_enabled, 'vector_rerank_count': result.meta.vector_rerank_count, 'vector_applied_count': result.meta.vector_applied_count, 'vector_skipped_count': result.meta.vector_skipped_count, 'vector_threshold': result.meta.vector_threshold, 'ranking_version': result.meta.ranking_version, 'ranking_components_enabled': result.meta.ranking_components_enabled if result.meta.ranking_components_enabled is not None else {}, 'errors': [error.to_dict() for error in result.meta.errors]}
        if progress_meta is not None:
            meta_payload['index_progress'] = progress_meta
        return JSONResponse({'error': {'code': first_error.code, 'message': first_error.message}, 'meta': meta_payload}, status_code=503)
    meta_payload = {'candidate_count': result.meta.candidate_count, 'resolved_count': result.meta.resolved_count, 'candidate_source': result.meta.candidate_source, 'fatal_error': result.meta.fatal_error, 'degraded': result.meta.degraded, 'error_count': result.meta.error_count, 'ranking_policy': result.meta.ranking_policy, 'rrf_k': result.meta.rrf_k, 'lsp_query_mode': result.meta.lsp_query_mode, 'lsp_sync_mode': result.meta.lsp_sync_mode, 'lsp_fallback_used': result.meta.lsp_fallback_used, 'lsp_fallback_reason': result.meta.lsp_fallback_reason, 'lsp_include_info_requested': result.meta.include_info_requested, 'lsp_symbol_info_budget_sec': result.meta.symbol_info_budget_sec, 'lsp_symbol_info_requested_count': result.meta.symbol_info_requested_count, 'lsp_symbol_info_budget_exceeded_count': result.meta.symbol_info_budget_exceeded_count, 'lsp_symbol_info_skipped_count': result.meta.symbol_info_skipped_count, 'importance_policy': result.meta.importance_policy, 'importance_weights': result.meta.importance_weights, 'importance_normalize_mode': result.meta.importance_normalize_mode, 'importance_max_boost': result.meta.importance_max_boost, 'vector_enabled': result.meta.vector_enabled, 'vector_rerank_count': result.meta.vector_rerank_count, 'vector_applied_count': result.meta.vector_applied_count, 'vector_skipped_count': result.meta.vector_skipped_count, 'vector_threshold': result.meta.vector_threshold, 'ranking_version': result.meta.ranking_version, 'ranking_components_enabled': result.meta.ranking_components_enabled if result.meta.ranking_components_enabled is not None else {}, 'errors': [error.to_dict() for error in result.meta.errors]}
    if progress_meta is not None:
        meta_payload['index_progress'] = progress_meta
    return JSONResponse({'items': [_build_search_item_payload(context=context, repo_root=repo, item=item) for item in result.items], 'meta': meta_payload})


def _build_search_item_payload(context: HttpContext, repo_root: str, item: object) -> dict[str, object]:
    payload: dict[str, object] = {
        'type': getattr(item, 'item_type'),
        'repo': getattr(item, 'repo'),
        'relative_path': getattr(item, 'relative_path'),
        'score': getattr(item, 'score'),
        'source': getattr(item, 'source'),
        'name': getattr(item, 'name'),
        'kind': getattr(item, 'kind'),
        'symbol_info': getattr(item, 'symbol_info'),
    }
    db_path = context.db_path
    if db_path is None:
        return payload
    content_hash = getattr(item, 'content_hash', None)
    relative_path = getattr(item, 'relative_path', None)
    if not isinstance(content_hash, str) or content_hash.strip() == '':
        return payload
    if not isinstance(relative_path, str) or relative_path.strip() == '':
        return payload
    workspace = context.workspace_repo.get_by_path(repo_root)
    if workspace is None:
        return payload
    snapshot = ToolDataLayerRepository(db_path).load_effective_snapshot(
        workspace_id=workspace.path,
        repo_root=repo_root,
        relative_path=relative_path,
        content_hash=content_hash,
    )
    l4_snapshot = snapshot.get('l4')
    if isinstance(l4_snapshot, dict):
        payload['l4'] = l4_snapshot
    l5_snapshot = snapshot.get('l5', [])
    if isinstance(l5_snapshot, list) and len(l5_snapshot) > 0:
        payload['l5'] = l5_snapshot
    _attach_single_line_policy(payload=payload, item=item, snapshot=snapshot)
    return payload


def _attach_single_line_policy(*, payload: dict[str, object], item: object, snapshot: dict[str, object]) -> None:
    # NOTE(policy): External API must expose a single canonical line only.
    # We intentionally prefer L3(AST/text) coordinates for editing safety across languages.
    # L5/LSP semantic coordinates are internal hints and must not be exposed as a second line.
    if str(payload.get("type", "")) != "symbol":
        return
    line, end_line = _resolve_canonical_line(item=item, snapshot=snapshot)
    if line is None:
        return
    payload["line"] = int(line)
    payload["end_line"] = int(end_line if end_line is not None else line)


def _resolve_canonical_line(*, item: object, snapshot: dict[str, object]) -> tuple[int | None, int | None]:
    l3 = snapshot.get("l3")
    name = getattr(item, "name", None)
    kind = getattr(item, "kind", None)
    if isinstance(l3, dict):
        symbols = l3.get("symbols")
        if isinstance(symbols, list):
            for symbol in symbols:
                if not isinstance(symbol, dict):
                    continue
                symbol_name = symbol.get("name")
                symbol_kind = symbol.get("kind")
                if isinstance(name, str) and name.strip() != "" and str(symbol_name) != name:
                    continue
                if isinstance(kind, str) and kind.strip() != "" and str(symbol_kind) != kind:
                    continue
                try:
                    line = int(symbol.get("line", 0))
                    end_line = int(symbol.get("end_line", line))
                except (TypeError, ValueError):
                    continue
                if line > 0:
                    return (line, end_line if end_line >= line else line)
    raw_line = getattr(item, "line", None)
    raw_end_line = getattr(item, "end_line", None)
    try:
        if raw_line is not None:
            line = int(raw_line)
            if line > 0:
                end_line = int(raw_end_line) if raw_end_line is not None else line
                return (line, end_line if end_line >= line else line)
    except (TypeError, ValueError):
        return (None, None)
    return (None, None)
def _search_progress_meta(context: HttpContext) -> dict[str, object] | None:
    if context.file_collection_service is None:
        return None
    payload = context.file_collection_service.get_pipeline_metrics().to_dict()
    def _safe_float(value: object, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return default
            try:
                return float(stripped)
            except ValueError:
                return default
        return default

    def _safe_int(value: object, default: int) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return default
            try:
                return int(stripped)
            except ValueError:
                return default
        return default

    raw_worker_state = payload.get('worker_state', 'unknown')
    worker_state = raw_worker_state if isinstance(raw_worker_state, str) and raw_worker_state.strip() != '' else 'unknown'
    return {
        'progress_percent_l2': _safe_float(payload.get('progress_percent_l2', 0.0), 0.0),
        'progress_percent_l3': _safe_float(payload.get('progress_percent_l3', 0.0), 0.0),
        'eta_l2_sec': _safe_int(payload.get('eta_l2_sec', -1), -1),
        'eta_l3_sec': _safe_int(payload.get('eta_l3_sec', -1), -1),
        'remaining_jobs_l2': _safe_int(payload.get('remaining_jobs_l2', 0), 0),
        'remaining_jobs_l3': _safe_int(payload.get('remaining_jobs_l3', 0), 0),
        'worker_state': worker_state,
    }
async def pipeline_policy_get_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    return JSONResponse({'policy': context.pipeline_control_service.get_policy().to_dict()})
async def pipeline_policy_set_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    deletion_raw = str(request.query_params.get('deletion_hold', '')).strip().lower()
    deletion_hold: bool | None = None
    if deletion_raw != '':
        if deletion_raw in {'on', 'true', '1'}:
            deletion_hold = True
        elif deletion_raw in {'off', 'false', '0'}:
            deletion_hold = False
        else:
            error = ErrorResponseDTO(code='ERR_POLICY_INVALID', message='deletion_hold는 on/off여야 합니다')
            return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    def _parse_int(name: str) -> tuple[int | None, ErrorResponseDTO | None]:
        raw = str(request.query_params.get(name, '')).strip()
        if raw == '':
            return (None, None)
        try:
            return (int(raw), None)
        except ValueError:
            return (None, ErrorResponseDTO(code='ERR_POLICY_INVALID', message=f'{name}는 정수여야 합니다'))
    l3_p95_ms, err = _parse_int('l3_p95_threshold_ms')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    dead_ratio_bps, err = _parse_int('dead_ratio_threshold_bps')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    workers, err = _parse_int('workers')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    watcher_queue_max, err = _parse_int('watcher_queue_max')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    watcher_overflow_rescan_cooldown_sec, err = _parse_int('watcher_overflow_rescan_cooldown_sec')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    bootstrap_l3_worker_count, err = _parse_int('bootstrap_l3_worker_count')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    bootstrap_l3_queue_max, err = _parse_int('bootstrap_l3_queue_max')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    bootstrap_exit_min_l2_coverage_bps, err = _parse_int('bootstrap_exit_min_l2_coverage_bps')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    bootstrap_exit_max_sec, err = _parse_int('bootstrap_exit_max_sec')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    alert_window_sec, err = _parse_int('alert_window_sec')
    if err is not None:
        return JSONResponse({'error': {'code': err.code, 'message': err.message}}, status_code=400)
    bootstrap_mode_raw = str(request.query_params.get('bootstrap_mode_enabled', '')).strip().lower()
    bootstrap_mode_enabled: bool | None = None
    if bootstrap_mode_raw != '':
        if bootstrap_mode_raw in {'on', 'true', '1'}:
            bootstrap_mode_enabled = True
        elif bootstrap_mode_raw in {'off', 'false', '0'}:
            bootstrap_mode_enabled = False
        else:
            return JSONResponse({'error': {'code': 'ERR_POLICY_INVALID', 'message': 'bootstrap_mode_enabled는 불리언이어야 합니다'}}, status_code=400)
    try:
        updated = context.pipeline_control_service.update_policy(deletion_hold=deletion_hold, l3_p95_threshold_ms=l3_p95_ms, dead_ratio_threshold_bps=dead_ratio_bps, enrich_worker_count=workers, watcher_queue_max=watcher_queue_max, watcher_overflow_rescan_cooldown_sec=watcher_overflow_rescan_cooldown_sec, bootstrap_mode_enabled=bootstrap_mode_enabled, bootstrap_l3_worker_count=bootstrap_l3_worker_count, bootstrap_l3_queue_max=bootstrap_l3_queue_max, bootstrap_exit_min_l2_coverage_bps=bootstrap_exit_min_l2_coverage_bps, bootstrap_exit_max_sec=bootstrap_exit_max_sec, alert_window_sec=alert_window_sec)
    except ValidationError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'policy': updated.to_dict()})
async def pipeline_alert_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    snapshot = context.pipeline_control_service.get_alert_status()
    return JSONResponse({'alert': snapshot.to_dict()})
async def pipeline_dead_list_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    limit_raw = str(request.query_params.get('limit', '20'))
    try:
        limit = int(limit_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT', message='limit는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    try:
        items = context.pipeline_control_service.list_dead_jobs(repo_root=repo, limit=limit)
    except ValidationError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    queue_snapshot = context.pipeline_control_service.get_queue_snapshot()
    return JSONResponse(
        {
            'items': [item.to_dict() for item in items],
            'meta': {
                'queue_snapshot': queue_snapshot,
                'executed_at': datetime.now(timezone.utc).isoformat(),
                'repo_scope': 'repo',
            },
        }
    )
async def pipeline_dead_requeue_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    limit_raw = str(request.query_params.get('limit', '20'))
    try:
        limit = int(limit_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT', message='limit는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    all_raw = str(request.query_params.get('all', 'false')).strip().lower()
    all_scopes = all_raw in {'true', '1', 'on', 'yes'}
    try:
        result = context.pipeline_control_service.requeue_dead_jobs(repo_root=repo, limit=limit, all_scopes=all_scopes)
    except ValidationError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'result': result.to_dict(), 'meta': {'queue_snapshot': result.queue_snapshot, 'executed_at': result.executed_at, 'repo_scope': result.repo_scope}})
async def pipeline_dead_purge_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    limit_raw = str(request.query_params.get('limit', '20'))
    try:
        limit = int(limit_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT', message='limit는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    all_raw = str(request.query_params.get('all', 'false')).strip().lower()
    all_scopes = all_raw in {'true', '1', 'on', 'yes'}
    try:
        result = context.pipeline_control_service.purge_dead_jobs(repo_root=repo, limit=limit, all_scopes=all_scopes)
    except ValidationError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'result': result.to_dict(), 'meta': {'queue_snapshot': result.queue_snapshot, 'executed_at': result.executed_at, 'repo_scope': result.repo_scope}})
async def pipeline_auto_status_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    return JSONResponse(
        {
            'auto_control': context.pipeline_control_service.get_auto_control_state().to_dict(),
            'stage_rollout': context.pipeline_control_service.get_stage_rollout_state(),
        }
    )
async def pipeline_auto_set_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    enabled_raw = str(request.query_params.get('enabled', '')).strip().lower()
    if enabled_raw in {'on', 'true', '1'}:
        enabled = True
    elif enabled_raw in {'off', 'false', '0'}:
        enabled = False
    else:
        error = ErrorResponseDTO(code='ERR_POLICY_INVALID', message='enabled는 on/off여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    updated = context.pipeline_control_service.set_auto_hold_enabled(enabled)
    return JSONResponse({'auto_control': updated.to_dict()})
async def pipeline_auto_tick_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_control_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_ALERT_UNAVAILABLE', message='pipeline control is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    return JSONResponse(context.pipeline_control_service.evaluate_auto_hold())
async def pipeline_quality_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_quality_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_QUALITY_UNAVAILABLE', message='pipeline quality is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    limit_files_raw = str(request.query_params.get('limit_files', '2000')).strip()
    profile = str(request.query_params.get('profile', 'default')).strip()
    try:
        limit_files = int(limit_files_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_LIMIT_FILES', message='limit_files는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    language_filter, filter_error = read_language_filter_from_query(request)
    if filter_error is not None:
        return JSONResponse({'error': {'code': filter_error.code, 'message': filter_error.message}}, status_code=400)
    try:
        summary = context.pipeline_quality_service.run(repo_root=repo, limit_files=limit_files, profile=profile, language_filter=language_filter)
    except QualityError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'quality': summary})
async def pipeline_perf_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_perf_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_PERF_UNAVAILABLE', message='pipeline perf is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    target_files_raw = str(request.query_params.get('target_files', '2000')).strip()
    profile = str(request.query_params.get('profile', 'realistic_v1')).strip()
    dataset_mode = str(request.query_params.get('dataset_mode', 'isolated')).strip().lower()
    fresh_db, fresh_db_error = parse_bool_value(request.query_params.get('fresh_db'), error_code='ERR_INVALID_FRESH_DB', field_name='fresh_db')
    if fresh_db_error is not None:
        return JSONResponse({'error': {'code': fresh_db_error.code, 'message': fresh_db_error.message}}, status_code=400)
    reset_probe_state, reset_probe_state_error = parse_bool_value(request.query_params.get('reset_probe_state'), error_code='ERR_INVALID_RESET_PROBE_STATE', field_name='reset_probe_state')
    if reset_probe_state_error is not None:
        return JSONResponse({'error': {'code': reset_probe_state_error.code, 'message': reset_probe_state_error.message}}, status_code=400)
    cold_lsp_reset, cold_lsp_reset_error = parse_bool_value(request.query_params.get('cold_lsp_reset'), error_code='ERR_INVALID_COLD_LSP_RESET', field_name='cold_lsp_reset')
    if cold_lsp_reset_error is not None:
        return JSONResponse({'error': {'code': cold_lsp_reset_error.code, 'message': cold_lsp_reset_error.message}}, status_code=400)
    if dataset_mode not in ('isolated', 'legacy'):
        error = ErrorResponseDTO(code='ERR_INVALID_DATASET_MODE', message='dataset_mode must be isolated or legacy')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    try:
        target_files = int(target_files_raw)
    except ValueError:
        error = ErrorResponseDTO(code='ERR_INVALID_TARGET_FILES', message='target_files는 정수여야 합니다')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    try:
        summary = context.pipeline_perf_service.run(
            repo_root=repo,
            target_files=target_files,
            profile=profile,
            dataset_mode=dataset_mode,
            fresh_db=fresh_db,
            reset_probe_state=reset_probe_state,
            cold_lsp_reset=cold_lsp_reset,
        )
    except PerfError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'perf': summary})


async def pipeline_perf_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_perf_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_PERF_UNAVAILABLE', message='pipeline perf is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    try:
        summary = context.pipeline_perf_service.get_latest_report()
    except PerfError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=404)
    return JSONResponse({'perf': summary})
async def pipeline_quality_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_quality_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_QUALITY_UNAVAILABLE', message='pipeline quality is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    try:
        summary = context.pipeline_quality_service.get_latest_report(repo_root=repo)
    except QualityError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=404)
    return JSONResponse({'quality': summary})
async def pipeline_lsp_matrix_run_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_lsp_matrix_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_LSP_MATRIX_UNAVAILABLE', message='pipeline lsp matrix is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    required_languages, required_error = read_required_languages_from_query(request)
    if required_error is not None:
        return JSONResponse({'error': {'code': required_error.code, 'message': required_error.message}}, status_code=400)
    fail_on_unavailable, fail_error = parse_fail_on_unavailable_from_query(request)
    if fail_error is not None:
        return JSONResponse({'error': {'code': fail_error.code, 'message': fail_error.message}}, status_code=400)
    strict_all_languages, strict_error = parse_strict_all_languages_from_query(request)
    if strict_error is not None:
        return JSONResponse({'error': {'code': strict_error.code, 'message': strict_error.message}}, status_code=400)
    strict_symbol_gate, strict_symbol_gate_error = parse_strict_symbol_gate_from_query(request)
    if strict_symbol_gate_error is not None:
        return JSONResponse({'error': {'code': strict_symbol_gate_error.code, 'message': strict_symbol_gate_error.message}}, status_code=400)
    try:
        summary = context.pipeline_lsp_matrix_service.run(repo_root=repo, required_languages=required_languages, fail_on_unavailable=fail_on_unavailable, strict_all_languages=strict_all_languages, strict_symbol_gate=strict_symbol_gate)
    except DaemonError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
    return JSONResponse({'lsp_matrix': summary})
async def pipeline_lsp_matrix_report_api_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    if context.pipeline_lsp_matrix_service is None:
        error = ErrorResponseDTO(code='ERR_PIPELINE_LSP_MATRIX_UNAVAILABLE', message='pipeline lsp matrix is unavailable')
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=503)
    _repo_id, repo, _repo_key, error_response = resolve_repo_from_query(context, request)
    if error_response is not None:
        return error_response
    assert repo is not None
    try:
        summary = context.pipeline_lsp_matrix_service.get_latest_report(repo_root=repo)
    except DaemonError as exc:
        error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=404)
    return JSONResponse({'lsp_matrix': summary})
async def validation_error_endpoint_handler(request, exc: ValidationError) -> JSONResponse:
    del request
    error = ErrorResponseDTO(code=exc.context.code, message=exc.context.message)
    return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=400)
def create_app(context: HttpContext) -> Starlette:
    app = Starlette(debug=False, exception_handlers={ValidationError: validation_error_endpoint_handler}, middleware=[Middleware(BackgroundProxyMiddleware), Middleware(RuntimeSessionMiddleware, runtime_repo=context.runtime_repo)], routes=[Route('/health', health_endpoint), Route('/status', status_endpoint), Route('/workspaces', workspaces_endpoint), Route('/mcp', mcp_jsonrpc_endpoint, methods=['POST']), Route('/search', search_endpoint), Route('/read', read_endpoint, methods=['GET']), Route('/read_file', read_file_endpoint, methods=['GET']), Route('/read_symbol', read_symbol_endpoint, methods=['GET']), Route('/read_snippet', read_snippet_endpoint, methods=['GET']), Route('/read_diff_preview', read_diff_preview_endpoint, methods=['POST']), Route('/errors', errors_endpoint), Route('/rescan', rescan_endpoint), Route('/repo-candidates', repo_candidates_endpoint), Route('/doctor', doctor_endpoint), Route('/daemon/list', daemon_list_endpoint), Route('/daemon/reconcile', daemon_reconcile_endpoint, methods=['POST']), Route('/pipeline/policy', pipeline_policy_get_endpoint, methods=['GET']), Route('/pipeline/policy', pipeline_policy_set_endpoint, methods=['POST']), Route('/pipeline/alert', pipeline_alert_endpoint, methods=['GET']), Route('/pipeline/dead', pipeline_dead_list_endpoint, methods=['GET']), Route('/pipeline/dead/requeue', pipeline_dead_requeue_endpoint, methods=['POST']), Route('/pipeline/dead/purge', pipeline_dead_purge_endpoint, methods=['POST']), Route('/pipeline/auto/status', pipeline_auto_status_endpoint, methods=['GET']), Route('/pipeline/auto/set', pipeline_auto_set_endpoint, methods=['POST']), Route('/pipeline/auto/tick', pipeline_auto_tick_endpoint, methods=['POST']), Route('/api/pipeline/errors', pipeline_errors_api_endpoint, methods=['GET']), Route('/api/pipeline/errors/{event_id:str}', pipeline_error_detail_api_endpoint, methods=['GET']), Route('/api/pipeline/perf/run', pipeline_perf_run_api_endpoint, methods=['POST']), Route('/api/pipeline/perf', pipeline_perf_report_api_endpoint, methods=['GET']), Route('/api/pipeline/quality/run', pipeline_quality_run_api_endpoint, methods=['POST']), Route('/api/pipeline/quality', pipeline_quality_report_api_endpoint, methods=['GET']), Route('/api/pipeline/lsp-matrix/run', pipeline_lsp_matrix_run_api_endpoint, methods=['POST']), Route('/api/pipeline/lsp-matrix', pipeline_lsp_matrix_report_api_endpoint, methods=['GET']), Route('/pipeline/errors', pipeline_errors_html_endpoint, methods=['GET']), Route('/pipeline/errors/{event_id:str}', pipeline_error_detail_html_endpoint, methods=['GET'])])
    app.state.context = context
    return app
