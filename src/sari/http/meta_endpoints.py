"""메타/상태 관련 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

from datetime import datetime, timezone
import os

from starlette.responses import JSONResponse

from sari import __version__
from sari.core.models import HealthResponseDTO
from sari.http.context import HttpContext


async def health_endpoint(request) -> JSONResponse:
    payload = HealthResponseDTO(status="ok", version=__version__, uptime_sec=0.0)
    return JSONResponse({"status": payload.status, "version": payload.version, "uptime_sec": payload.uptime_sec})


async def status_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    stale_timeout_sec = _resolve_stale_timeout_sec(context)
    runtime = context.runtime_repo.get_runtime()
    workspaces = context.workspace_repo.list_all()
    presentation_service = context.resolve_http_presentation_service()
    language_support = presentation_service.build_language_support_payload()
    lsp_metrics = context.lsp_metrics_provider() if context.lsp_metrics_provider is not None else {}
    reconcile_state = context.admin_service.get_runtime_reconcile_state()
    auto_control = None
    stage_rollout = None
    l5_admission = None
    if context.pipeline_control_service is not None:
        auto_control = context.pipeline_control_service.get_auto_control_state().to_dict()
        stage_rollout = context.pipeline_control_service.get_stage_rollout_state()
    if context.file_collection_service is not None:
        l5_status_getter = getattr(context.file_collection_service, "get_l5_admission_status", None)
        if callable(l5_status_getter):
            try:
                payload = l5_status_getter()
                if isinstance(payload, dict):
                    l5_admission = payload
            except (RuntimeError, OSError, ValueError, TypeError):
                l5_admission = None
    if runtime is None:
        metrics = None
        if context.file_collection_service is not None:
            metrics = context.file_collection_service.get_pipeline_metrics().to_dict()
        return JSONResponse(
            {
                "daemon": None,
                "workspace_count": len(workspaces),
                "phase": "phase2",
                "run_mode": context.admin_service.run_mode(),
                "pipeline_metrics": metrics,
                "language_support": language_support,
                "daemon_lifecycle": None,
                "lsp_metrics": lsp_metrics,
                "reconcile_state": reconcile_state,
                "auto_control": auto_control,
                "stage_rollout": stage_rollout,
                "l5_admission": l5_admission,
            }
        )
    metrics = None
    if context.file_collection_service is not None:
        metrics = context.file_collection_service.get_pipeline_metrics().to_dict()
    pid_alive = _is_pid_alive(runtime.pid)
    heartbeat_age_sec = _heartbeat_age_sec(runtime.last_heartbeat_at)
    lease_valid = _lease_valid(runtime.lease_expires_at)
    health_state = _daemon_health_state(
        runtime=runtime,
        stale_timeout_sec=stale_timeout_sec,
        pid_alive=pid_alive,
        heartbeat_age_sec=heartbeat_age_sec,
        lease_valid=lease_valid,
    )
    status_reason = _daemon_status_reason(runtime=runtime, stale_timeout_sec=stale_timeout_sec, health_state=health_state, lease_valid=lease_valid)
    return JSONResponse(
        {
            "daemon": {
                "pid": runtime.pid,
                "host": runtime.host,
                "port": runtime.port,
                "state": runtime.state,
                "started_at": runtime.started_at,
                "session_count": runtime.session_count,
                "last_heartbeat_at": runtime.last_heartbeat_at,
                "last_exit_reason": runtime.last_exit_reason,
                "lease_token": runtime.lease_token,
                "owner_generation": runtime.owner_generation,
                "lease_expires_at": runtime.lease_expires_at,
            },
            "workspace_count": len(workspaces),
            "phase": "phase2",
            "run_mode": context.admin_service.run_mode(),
            "pipeline_metrics": metrics,
            "language_support": language_support,
            "daemon_lifecycle": {
                "last_heartbeat_at": runtime.last_heartbeat_at,
                "heartbeat_age_sec": heartbeat_age_sec,
                "last_exit_reason": runtime.last_exit_reason,
                "health_state": health_state,
                "status_reason": status_reason,
                "pid_alive": pid_alive,
                "lease_valid": lease_valid,
            },
            "lsp_metrics": lsp_metrics,
            "reconcile_state": reconcile_state,
            "auto_control": auto_control,
            "stage_rollout": stage_rollout,
            "l5_admission": l5_admission,
        }
    )


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


async def workspaces_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    items = context.workspace_repo.list_all()
    return JSONResponse({"items": [{"path": item.path, "name": item.name, "indexed_at": item.indexed_at, "is_active": item.is_active} for item in items]})


def _heartbeat_age_sec(last_heartbeat_at: str) -> float:
    try:
        parsed = datetime.fromisoformat(last_heartbeat_at)
    except ValueError:
        return -1.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lease_valid(lease_expires_at: str | None) -> bool:
    if lease_expires_at is None or lease_expires_at.strip() == "":
        return True
    try:
        expires = datetime.fromisoformat(lease_expires_at)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires >= datetime.now(timezone.utc)


def _resolve_stale_timeout_sec(context: HttpContext) -> float:
    config = getattr(getattr(context, "admin_service", None), "_config", None)
    raw_timeout = getattr(config, "daemon_stale_timeout_sec", 15)
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        return 15.0
    return max(0.0, timeout)


def _daemon_health_state(  # noqa: ANN001
    *,
    runtime,
    stale_timeout_sec: float = 15.0,
    pid_alive: bool | None = None,
    heartbeat_age_sec: float | None = None,
    lease_valid: bool | None = None,
) -> str:
    if pid_alive is None:
        pid_alive = _is_pid_alive(int(runtime.pid))
    if not pid_alive:
        return "dead"
    heartbeat_age = _heartbeat_age_sec(str(runtime.last_heartbeat_at)) if heartbeat_age_sec is None else heartbeat_age_sec
    if heartbeat_age < 0:
        return "degraded"
    if heartbeat_age > stale_timeout_sec:
        return "stale"
    lease_ok = _lease_valid(getattr(runtime, "lease_expires_at", None)) if lease_valid is None else lease_valid
    if not lease_ok:
        return "degraded"
    return "running"


def _daemon_status_reason(  # noqa: ANN001
    *,
    runtime,
    stale_timeout_sec: float = 15.0,
    health_state: str | None = None,
    lease_valid: bool | None = None,
) -> str:
    state = _daemon_health_state(runtime=runtime, stale_timeout_sec=stale_timeout_sec) if health_state is None else health_state
    if state == "dead":
        return "process_dead"
    if state == "stale":
        return "heartbeat_stale_but_pid_alive"
    lease_ok = _lease_valid(getattr(runtime, "lease_expires_at", None)) if lease_valid is None else lease_valid
    if state == "degraded" and not lease_ok:
        return "lease_invalid_but_pid_alive"
    if state == "degraded":
        return "heartbeat_parse_error"
    return "running"
