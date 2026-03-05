"""메타/상태 관련 HTTP 엔드포인트를 제공한다."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import sqlite3

from starlette.responses import JSONResponse

from sari import __version__
from sari.core.models import HealthResponseDTO
from sari.http.context import HttpContext
from sari.services.daemon.health import evaluate_daemon_health


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
    registry_snapshot = _registry_snapshot(context=context, pid=runtime.pid)
    registry_degraded = _registry_degraded(snapshot=registry_snapshot)
    health_payload = evaluate_daemon_health(
        pid_alive=pid_alive,
        heartbeat_age_sec=heartbeat_age_sec,
        stale_timeout_sec=stale_timeout_sec,
        lease_valid=lease_valid,
        registry_degraded=registry_degraded,
        status_reason_detail=_registry_status_reason_detail(snapshot=registry_snapshot),
    )
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
                "health_state": health_payload["health_state"],
                "status_reason": health_payload["status_reason"],
                "pid_alive": health_payload["pid_alive"],
                "lease_valid": health_payload["lease_valid"],
                "health_signals": health_payload["health_signals"],
                "status_reason_detail": health_payload["status_reason_detail"],
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
    registry_degraded: bool | None = None,
) -> str:
    resolved_pid_alive = _is_pid_alive(int(runtime.pid)) if pid_alive is None else bool(pid_alive)
    heartbeat_age = _heartbeat_age_sec(str(runtime.last_heartbeat_at)) if heartbeat_age_sec is None else float(heartbeat_age_sec)
    lease_ok = _lease_valid(getattr(runtime, "lease_expires_at", None)) if lease_valid is None else bool(lease_valid)
    payload = evaluate_daemon_health(
        pid_alive=resolved_pid_alive,
        heartbeat_age_sec=heartbeat_age,
        stale_timeout_sec=stale_timeout_sec,
        lease_valid=lease_ok,
        registry_degraded=bool(registry_degraded),
    )
    return str(payload["health_state"])


def _daemon_status_reason(  # noqa: ANN001
    *,
    runtime,
    stale_timeout_sec: float = 15.0,
    health_state: str | None = None,
    lease_valid: bool | None = None,
    registry_degraded: bool | None = None,
) -> str:
    state = _daemon_health_state(runtime=runtime, stale_timeout_sec=stale_timeout_sec) if health_state is None else str(health_state)
    if state == "dead":
        return "process_dead"
    if state == "stale":
        return "heartbeat_stale_but_pid_alive"
    lease_ok = _lease_valid(getattr(runtime, "lease_expires_at", None)) if lease_valid is None else lease_valid
    if state == "degraded" and not lease_ok:
        return "lease_invalid_but_pid_alive"
    if state == "degraded" and bool(registry_degraded):
        return "registry_degraded_but_pid_alive"
    if state == "degraded":
        return "heartbeat_parse_error"
    return "running"


def _registry_degraded(*, snapshot: dict[str, object] | None) -> bool:
    if snapshot is None:
        return False
    return str(snapshot.get("deployment_state", "ACTIVE")).upper() != "ACTIVE"


def _registry_status_reason_detail(*, snapshot: dict[str, object] | None) -> dict[str, object]:
    if snapshot is None:
        return {"deployment_state": None, "health_fail_streak": None, "last_health_error": None}
    return {
        "deployment_state": snapshot.get("deployment_state"),
        "health_fail_streak": snapshot.get("health_fail_streak"),
        "last_health_error": snapshot.get("last_health_error"),
    }


def _registry_snapshot(*, context: HttpContext, pid: int) -> dict[str, object] | None:
    registry_repo = getattr(getattr(context, "admin_service", None), "_registry_repo", None)
    if registry_repo is None:
        return None
    list_all = getattr(registry_repo, "list_all", None)
    if not callable(list_all):
        return None
    try:
        entries = list_all()
    except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError):
        return None
    for entry in entries:
        if int(getattr(entry, "pid", -1)) != int(pid):
            continue
        return {
            "deployment_state": getattr(entry, "deployment_state", None),
            "health_fail_streak": getattr(entry, "health_fail_streak", None),
            "last_health_error": getattr(entry, "last_health_error", None),
        }
    return None
