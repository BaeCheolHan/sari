"""Shared status payload helpers for sync/async HTTP servers."""

from __future__ import annotations

from typing import Any, Callable

from sari.core.daemon_status_projection import build_daemon_state_projection
from sari.core.error_contract_metrics import snapshot_error_contract_metrics
from sari.core.fallback_governance import fallback_taxonomy, snapshot_fallback_metrics


def build_runtime_status(indexer: Any, warn_status: Callable[..., None] | None = None) -> dict[str, Any]:
    runtime_status: dict[str, Any] = {}
    if not hasattr(indexer, "get_runtime_status"):
        return runtime_status
    try:
        raw_runtime = indexer.get_runtime_status()
        if isinstance(raw_runtime, dict):
            runtime_status = raw_runtime
    except (AttributeError, TypeError, ValueError) as e:
        if warn_status is not None:
            warn_status(
                "INDEXER_RUNTIME_STATUS_FAILED",
                "Failed to resolve indexer runtime status; using base status",
                error=repr(e),
            )
    return runtime_status


def build_performance_payload(indexer: Any) -> dict[str, Any]:
    if not hasattr(indexer, "get_performance_metrics"):
        return {}
    try:
        raw_perf = indexer.get_performance_metrics()
        return dict(raw_perf) if isinstance(raw_perf, dict) else {}
    except (AttributeError, TypeError, ValueError):
        return {}


def build_queue_depths_payload(indexer: Any, db: Any | None = None, *, fallback: bool = False) -> dict[str, int]:
    queue_depths: dict[str, int] = {}
    if hasattr(indexer, "get_queue_depths"):
        try:
            raw_depths = indexer.get_queue_depths()
            if isinstance(raw_depths, dict):
                queue_depths = {
                    str(k): int(v or 0)
                    for k, v in raw_depths.items()
                    if isinstance(k, str)
                }
        except (AttributeError, TypeError, ValueError):
            queue_depths = {}

    if queue_depths or not fallback:
        return queue_depths

    if db is not None:
        writer = getattr(db, "writer", None)
        if writer is not None and hasattr(writer, "qsize"):
            try:
                queue_depths["db_writer"] = int(writer.qsize() or 0)
            except (TypeError, ValueError):
                pass

    worker_proc = getattr(indexer, "_worker_proc", None)
    worker_alive = bool(worker_proc and worker_proc.is_alive())
    queue_depths["index_worker"] = 1 if worker_alive else 0
    queue_depths["rescan_pending"] = 1 if bool(getattr(indexer, "_pending_rescan", False)) else 0
    return queue_depths


def build_orphan_daemon_warnings(orphan_daemons: list[dict[str, Any]]) -> list[str]:
    return [
        f"Orphan daemon PID {d.get('pid')} detected (not in registry)"
        for d in orphan_daemons
    ]


def build_status_payload_base(
    *,
    host: str,
    port: int,
    version: str,
    status_obj: Any,
    runtime_status: dict[str, Any],
    base_last_scan_ts: int,
    total_db_files: int,
    orphan_daemons: list[dict[str, Any]],
    orphan_daemon_warnings: list[str],
    daemon_status: Any,
    performance: dict[str, Any],
    queue_depths: dict[str, int],
    repo_stats: dict[str, Any],
    roots: list[dict[str, Any]],
    system_metrics: dict[str, Any],
    status_warning_counts: dict[str, int],
    warning_counts: dict[str, int],
    warnings_recent: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace_root = ""
    daemon_target_host = str(host)
    daemon_target_port = int(port)
    if extra and isinstance(extra, dict):
        workspace_root = str(extra.get("workspace_root") or "")
        daemon_target_host = str(extra.get("daemon_target_host") or daemon_target_host)
        try:
            daemon_target_port = int(extra.get("daemon_target_port") or daemon_target_port)
        except (TypeError, ValueError):
            daemon_target_port = int(port)
    projection = build_daemon_state_projection(
        host=daemon_target_host,
        port=daemon_target_port,
        workspace_root=workspace_root or None,
    )
    top_reasons = sorted(
        ((str(k), int(v or 0)) for k, v in (warning_counts or {}).items()),
        key=lambda item: item[1],
        reverse=True,
    )[:10]
    error_contract = snapshot_error_contract_metrics()
    payload: dict[str, Any] = {
        "ok": True,
        "host": host,
        "port": port,
        "version": version,
        "index_ready": bool(runtime_status.get("index_ready", bool(getattr(status_obj, "index_ready", False)))),
        "last_scan_ts": int(runtime_status.get("scan_finished_ts", base_last_scan_ts) or 0),
        "scanned_files": int(runtime_status.get("scanned_files", getattr(status_obj, "scanned_files", 0)) or 0),
        "indexed_files": int(runtime_status.get("indexed_files", getattr(status_obj, "indexed_files", 0)) or 0),
        "symbols_extracted": int(runtime_status.get("symbols_extracted", getattr(status_obj, "symbols_extracted", 0)) or 0),
        "total_files_db": int(total_db_files or 0),
        "errors": int(runtime_status.get("errors", getattr(status_obj, "errors", 0)) or 0),
        "status_source": str(runtime_status.get("status_source", "indexer_status") or "indexer_status"),
        "orphan_daemon_count": len(orphan_daemons),
        "orphan_daemon_warnings": orphan_daemon_warnings,
        "signals_disabled": daemon_status.signals_disabled,
        "shutdown_intent": daemon_status.shutdown_intent,
        "suicide_state": daemon_status.suicide_state,
        "active_leases_count": daemon_status.active_leases_count,
        "leases": list(daemon_status.leases or []),
        "last_reap_at": daemon_status.last_reap_at,
        "reaper_last_run_at": daemon_status.reaper_last_run_at,
        "no_client_since": daemon_status.no_client_since,
        "grace_remaining": daemon_status.grace_remaining,
        "grace_remaining_ms": daemon_status.grace_remaining_ms,
        "shutdown_once_set": daemon_status.shutdown_once_set,
        "last_event_ts": daemon_status.last_event_ts,
        "event_queue_depth": daemon_status.event_queue_depth,
        "last_shutdown_reason": daemon_status.last_shutdown_reason,
        "shutdown_reason": daemon_status.shutdown_reason,
        "workers_alive": list(daemon_status.workers_alive or []),
        "performance": performance,
        "queue_depths": queue_depths,
        "repo_stats": repo_stats,
        "roots": roots,
        "system_metrics": system_metrics,
        "status_warning_counts": status_warning_counts,
        "warning_counts": warning_counts,
        "warnings_recent": warnings_recent,
        "registry_truth": projection["registry_truth"],
        "socket_truth": projection["socket_truth"],
        "process_truth": projection["process_truth"],
        "final_truth": projection["final_truth"],
        "mismatch_reason": projection["mismatch_reason"],
        "fallback_taxonomy": fallback_taxonomy(),
        "fallback_metrics": snapshot_fallback_metrics(),
        "error_contract_metrics": {
            "unknown_tool_error_count": int(error_contract.get("unknown_tool_error_count", 0) or 0),
            "reason_code_cardinality": len(warning_counts or {}),
            "top_reason_codes": [{"reason_code": code, "count": count} for code, count in top_reasons],
        },
    }
    if extra:
        payload.update(extra)
    return payload
