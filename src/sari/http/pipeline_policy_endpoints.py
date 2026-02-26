"""Pipeline policy/control 관련 HTTP 엔드포인트."""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.responses import JSONResponse

from sari.core.exceptions import ValidationError
from sari.http.context import HttpContext
from sari.http.pipeline_common import (
    error_response,
    parse_limit_or_error,
    parse_optional_int_params,
    parse_optional_onoff_bool,
    pipeline_control_or_error,
    validation_error_response,
)
from sari.http.request_parsers import resolve_repo_from_query


async def pipeline_policy_get_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    return JSONResponse({"policy": service.get_policy().to_dict()})


async def pipeline_policy_set_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    deletion_hold, deletion_hold_error = parse_optional_onoff_bool(
        raw_value=str(request.query_params.get("deletion_hold", "")),
        field_name="deletion_hold",
    )
    if deletion_hold_error is not None:
        return deletion_hold_error
    int_values, int_error = parse_optional_int_params(
        request,
        field_names=(
            "l3_p95_threshold_ms",
            "dead_ratio_threshold_bps",
            "workers",
            "watcher_queue_max",
            "watcher_overflow_rescan_cooldown_sec",
            "bootstrap_l3_worker_count",
            "bootstrap_l3_queue_max",
            "bootstrap_exit_min_l2_coverage_bps",
            "bootstrap_exit_max_sec",
            "alert_window_sec",
        ),
    )
    if int_error is not None:
        return int_error
    bootstrap_mode_enabled, bootstrap_mode_error = parse_optional_onoff_bool(
        raw_value=str(request.query_params.get("bootstrap_mode_enabled", "")),
        field_name="bootstrap_mode_enabled",
    )
    if bootstrap_mode_error is not None:
        return bootstrap_mode_error
    try:
        updated = service.update_policy(
            deletion_hold=deletion_hold,
            l3_p95_threshold_ms=int_values["l3_p95_threshold_ms"],
            dead_ratio_threshold_bps=int_values["dead_ratio_threshold_bps"],
            enrich_worker_count=int_values["workers"],
            watcher_queue_max=int_values["watcher_queue_max"],
            watcher_overflow_rescan_cooldown_sec=int_values["watcher_overflow_rescan_cooldown_sec"],
            bootstrap_mode_enabled=bootstrap_mode_enabled,
            bootstrap_l3_worker_count=int_values["bootstrap_l3_worker_count"],
            bootstrap_l3_queue_max=int_values["bootstrap_l3_queue_max"],
            bootstrap_exit_min_l2_coverage_bps=int_values["bootstrap_exit_min_l2_coverage_bps"],
            bootstrap_exit_max_sec=int_values["bootstrap_exit_max_sec"],
            alert_window_sec=int_values["alert_window_sec"],
        )
    except ValidationError as exc:
        return validation_error_response(exc)
    return JSONResponse({"policy": updated.to_dict()})


async def pipeline_alert_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    snapshot = service.get_alert_status()
    return JSONResponse({"alert": snapshot.to_dict()})


async def pipeline_dead_list_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    if repo is None:
        raise ValueError("resolve_repo_from_query returned no error but repo is None")
    limit, limit_error = parse_limit_or_error(request)
    if limit_error is not None:
        return limit_error
    try:
        items = service.list_dead_jobs(repo_root=repo, limit=limit)
    except ValidationError as exc:
        return validation_error_response(exc)
    queue_snapshot = service.get_queue_snapshot()
    return JSONResponse(
        {
            "items": [item.to_dict() for item in items],
            "meta": {
                "queue_snapshot": queue_snapshot,
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "repo_scope": "repo",
            },
        }
    )


async def pipeline_dead_requeue_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    if repo is None:
        raise ValueError("resolve_repo_from_query returned no error but repo is None")
    limit, limit_error = parse_limit_or_error(request)
    if limit_error is not None:
        return limit_error
    all_raw = str(request.query_params.get("all", "false")).strip().lower()
    all_scopes = all_raw in {"true", "1", "on", "yes"}
    try:
        result = service.requeue_dead_jobs(repo_root=repo, limit=limit, all_scopes=all_scopes)
    except ValidationError as exc:
        return validation_error_response(exc)
    return JSONResponse(
        {"result": result.to_dict(), "meta": {"queue_snapshot": result.queue_snapshot, "executed_at": result.executed_at, "repo_scope": result.repo_scope}}
    )


async def pipeline_dead_purge_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    _repo_id, repo, _repo_key, repo_error = resolve_repo_from_query(context, request)
    if repo_error is not None:
        return repo_error
    if repo is None:
        raise ValueError("resolve_repo_from_query returned no error but repo is None")
    limit, limit_error = parse_limit_or_error(request)
    if limit_error is not None:
        return limit_error
    all_raw = str(request.query_params.get("all", "false")).strip().lower()
    all_scopes = all_raw in {"true", "1", "on", "yes"}
    try:
        result = service.purge_dead_jobs(repo_root=repo, limit=limit, all_scopes=all_scopes)
    except ValidationError as exc:
        return validation_error_response(exc)
    return JSONResponse(
        {"result": result.to_dict(), "meta": {"queue_snapshot": result.queue_snapshot, "executed_at": result.executed_at, "repo_scope": result.repo_scope}}
    )


async def pipeline_auto_status_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    return JSONResponse(
        {
            "auto_control": service.get_auto_control_state().to_dict(),
            "stage_rollout": service.get_stage_rollout_state(),
        }
    )


async def pipeline_auto_set_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    enabled_raw = str(request.query_params.get("enabled", "")).strip().lower()
    if enabled_raw in {"on", "true", "1"}:
        enabled = True
    elif enabled_raw in {"off", "false", "0"}:
        enabled = False
    else:
        return error_response(code="ERR_POLICY_INVALID", message="enabled는 on/off여야 합니다", status_code=400)
    updated = service.set_auto_hold_enabled(enabled)
    return JSONResponse({"auto_control": updated.to_dict()})


async def pipeline_auto_tick_endpoint(request) -> JSONResponse:
    context: HttpContext = request.app.state.context
    service, error = pipeline_control_or_error(context)
    if error is not None:
        return error
    return JSONResponse(service.evaluate_auto_hold())
