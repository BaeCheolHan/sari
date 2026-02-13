"""Shared status payload helpers for sync/async HTTP servers."""

from __future__ import annotations

from typing import Any, Callable


def build_runtime_status(indexer: Any, warn_status: Callable[..., None] | None = None) -> dict[str, Any]:
    runtime_status: dict[str, Any] = {}
    if not hasattr(indexer, "get_runtime_status"):
        return runtime_status
    try:
        raw_runtime = indexer.get_runtime_status()
        if isinstance(raw_runtime, dict):
            runtime_status = raw_runtime
    except Exception as e:
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
    except Exception:
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
        except Exception:
            queue_depths = {}

    if queue_depths or not fallback:
        return queue_depths

    if db is not None:
        writer = getattr(db, "writer", None)
        if writer is not None and hasattr(writer, "qsize"):
            try:
                queue_depths["db_writer"] = int(writer.qsize() or 0)
            except Exception:
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
