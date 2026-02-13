from __future__ import annotations

import os
from typing import Any, Callable


def _load_configured_roots(workspace_root: str) -> tuple[list[str], Any | None]:
    try:
        from sari.core.workspace import WorkspaceManager
        from sari.core.config.main import Config

        base_root = workspace_root or WorkspaceManager.resolve_workspace_root()
        cfg_path = WorkspaceManager.resolve_config_path(base_root)
        cfg = Config.load(cfg_path, workspace_root_override=base_root)
        configured_roots = list(getattr(cfg, "workspace_roots", []) or [])
        return configured_roots, WorkspaceManager
    except Exception:
        return ([workspace_root] if workspace_root else []), None


def _parse_failed_row(row: object) -> tuple[str, int, int]:
    if isinstance(row, dict):
        rid = str(row.get("root_id") or "")
        pending_count = int(row.get("pending_count") or 0)
        failed_count = int(row.get("failed_count") or 0)
        return rid, pending_count, failed_count

    rid = str(getattr(row, "root_id", "") or "")
    if not rid and isinstance(row, (list, tuple)) and len(row) >= 1:
        rid = str(row[0] or "")
    pending_count = int(getattr(row, "pending_count", 0) or 0)
    failed_count = int(getattr(row, "failed_count", 0) or 0)
    if isinstance(row, (list, tuple)):
        if len(row) >= 2:
            pending_count = int(row[1] or 0)
        if len(row) >= 3:
            failed_count = int(row[2] or 0)
    return rid, pending_count, failed_count


def build_registered_workspaces_payload(
    *,
    workspace_root: str,
    db: Any,
    indexer: Any,
    normalize_workspace_path_with_meta: Callable[[str], tuple[str, str]],
    indexer_workspace_roots: Callable[[Any], list[str]],
    status_warning_counts_provider: Callable[[], dict[str, int]],
    warn_status: Callable[[str, str], None],
    worker_alive: bool = False,
    pending_rescan: bool = False,
    watched_roots_warn_code: str = "WATCHED_ROOTS_RESOLVE_FAILED",
    watched_roots_warn_message: str = "Failed while resolving watched workspace roots",
) -> dict[str, Any]:
    configured_roots, workspace_manager = _load_configured_roots(workspace_root)

    norm_roots: list[str] = []
    seen: set[str] = set()
    normalized_by_path: dict[str, str] = {}
    normalize_fallback_count = 0
    for root in configured_roots:
        if not root:
            continue
        normalized, normalized_by = normalize_workspace_path_with_meta(str(root))
        if normalized and normalized not in seen:
            seen.add(normalized)
            norm_roots.append(normalized)
            normalized_by_path[normalized] = normalized_by
        if normalized_by == "fallback":
            normalize_fallback_count += 1

    indexed_by_path: dict[str, Any] = {}
    row_parse_error_count = 0
    if hasattr(db, "get_roots"):
        try:
            rows = db.get_roots() or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    p = row.get("path") or row.get("root_path") or row.get("real_path")
                    if not p:
                        continue
                    normalized, normalized_by = normalize_workspace_path_with_meta(str(p))
                    indexed_by_path[normalized] = row
                    if normalized_by == "fallback":
                        normalize_fallback_count += 1
                except Exception as row_error:
                    row_parse_error_count += 1
                    warn_status(
                        "WORKSPACE_ROW_PARSE_FAILED",
                        "Failed to parse workspace root row",
                        error=repr(row_error),
                        raw_row=repr(row),
                    )
        except Exception as e:
            warn_status(
                "WORKSPACE_ROOTS_FETCH_FAILED",
                "Failed to load workspace roots from DB",
                error=repr(e),
            )

    failed_by_root: dict[str, dict[str, int]] = {}
    if hasattr(db, "execute"):
        try:
            failed_rows = db.execute(
                """
                SELECT
                    root_id,
                    SUM(CASE WHEN attempts < 3 THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN attempts >= 3 THEN 1 ELSE 0 END) AS failed_count
                FROM failed_tasks
                GROUP BY root_id
                """
            ).fetchall() or []
            for row in failed_rows:
                rid, pending_count, failed_count = _parse_failed_row(row)
                if rid:
                    failed_by_root[rid] = {
                        "pending_count": pending_count,
                        "failed_count": failed_count,
                    }
        except Exception as e:
            warn_status(
                "FAILED_TASKS_AGGREGATE_FAILED",
                "Failed to aggregate failed task counts",
                error=repr(e),
            )

    watched_roots = set()
    try:
        for root in indexer_workspace_roots(indexer):
            normalized, normalized_by = normalize_workspace_path_with_meta(str(root))
            watched_roots.add(normalized)
            if normalized_by == "fallback":
                normalize_fallback_count += 1
    except Exception as e:
        warn_status(
            watched_roots_warn_code,
            watched_roots_warn_message,
            error=repr(e),
        )

    items: list[dict[str, Any]] = []
    for root in norm_roots:
        abs_path = os.path.expanduser(root)
        exists = os.path.isdir(abs_path)
        readable = os.access(abs_path, os.R_OK | os.X_OK) if exists else False
        watched = root in watched_roots
        indexed_row = indexed_by_path.get(root)
        indexed = bool(indexed_row) and (
            int((indexed_row or {}).get("file_count", 0) or 0) > 0
            or int((indexed_row or {}).get("last_indexed_ts", 0) or 0) > 0
            or int((indexed_row or {}).get("updated_ts", 0) or 0) > 0
        )

        computed_root_id = ""
        if isinstance(indexed_row, dict):
            computed_root_id = str(indexed_row.get("root_id", "") or "")
        if not computed_root_id and workspace_manager is not None:
            try:
                computed_root_id = str(workspace_manager.root_id_for_workspace(root))
            except Exception:
                computed_root_id = ""

        failed_counts = failed_by_root.get(computed_root_id, {})
        if not exists:
            status = "missing"
            reason = "Path does not exist"
            index_state = "Unavailable"
        elif not readable:
            status = "blocked"
            reason = "Path is not readable"
            index_state = "Blocked"
        elif indexed and watched:
            status = "indexed"
            if worker_alive:
                reason = "Indexed in DB and currently re-indexing"
                index_state = "Re-indexing"
            else:
                reason = "Indexed in DB and watched"
                index_state = "Idle"
        elif indexed and not watched:
            status = "indexed_stale"
            reason = "Indexed in DB but not currently watched"
            index_state = "Stale"
        elif watched:
            status = "watching"
            if worker_alive:
                reason = "Watching workspace, initial indexing in progress"
                index_state = "Indexing"
            elif pending_rescan:
                reason = "Watching workspace, rescan queued"
                index_state = "Rescan Queued"
            else:
                reason = "Watching workspace, awaiting first index"
                index_state = "Initial Scan Pending"
        else:
            status = "registered"
            reason = "Configured but not currently watched"
            index_state = "Not Watching"

        items.append(
            {
                "path": root,
                "normalized_by": normalized_by_path.get(root, "workspace_manager"),
                "exists": bool(exists),
                "readable": bool(readable),
                "watched": bool(watched),
                "indexed": bool(indexed),
                "file_count": int((indexed_row or {}).get("file_count", 0) or 0)
                if isinstance(indexed_row, dict)
                else 0,
                "last_indexed_ts": int(
                    (indexed_row or {}).get("last_indexed_ts", 0)
                    or (indexed_row or {}).get("updated_ts", 0)
                    or 0
                )
                if isinstance(indexed_row, dict)
                else 0,
                "pending_count": int(failed_counts.get("pending_count", 0) or 0),
                "failed_count": int(failed_counts.get("failed_count", 0) or 0),
                "status": status,
                "reason": reason,
                "index_state": index_state,
                "root_id": computed_root_id,
            }
        )

    return {
        "workspaces": items,
        "normalization": {"fallback_count": int(normalize_fallback_count)},
        "row_parse_error_count": int(row_parse_error_count),
        "status_warning_counts": status_warning_counts_provider(),
    }
