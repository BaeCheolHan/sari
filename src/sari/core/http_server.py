import json
import os
import re
import threading
import mimetypes
import time
import logging
import datetime as _dt
from typing import Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from sari.version import __version__
from sari.core.warning_sink import warning_sink
from sari.core.utils.uuid7 import uuid7_hex

# Support script mode and package mode
try:
    from .db import LocalSearchDB  # type: ignore
    from .indexer import Indexer  # type: ignore
    from .models import SearchOptions  # type: ignore
    from .http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from .utils.system import get_system_metrics  # type: ignore
    from .daemon_health import detect_orphan_daemons  # type: ignore
    from .policy_engine import load_daemon_runtime_status  # type: ignore
except ImportError:
    from db import LocalSearchDB  # type: ignore
    from indexer import Indexer  # type: ignore
    from models import SearchOptions  # type: ignore
    from http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from utils.system import get_system_metrics  # type: ignore
    from daemon_health import detect_orphan_daemons  # type: ignore
    from policy_engine import load_daemon_runtime_status  # type: ignore


class Handler(BaseHTTPRequestHandler):
    # class attributes injected in `serve_forever`
    db: LocalSearchDB
    indexer: Indexer
    server_host: str = "127.0.0.1"
    server_port: int = 47777
    server_version: str = __version__
    root_ids: list[str] = []
    mcp_server = None
    middlewares = default_http_middlewares()
    start_time: float = time.time()
    workspace_root: str = ""
    shared_http_gateway: bool = False

    def _init_request_status(self):
        self._status_warning_counts = {}

    def _warn_status(self, code: str, message: str, **context):
        if not hasattr(self, "_status_warning_counts"):
            self._init_request_status()
        key = str(code or "UNKNOWN")
        self._status_warning_counts[key] = int(self._status_warning_counts.get(key, 0) or 0) + 1
        warning_sink.warn(
            reason_code=key,
            where="http_server",
            extra={"message": str(message), **dict(context)},
        )
        details = ", ".join(f"{k}={v!r}" for k, v in context.items())
        if details:
            logging.getLogger("sari.http_server").warning("%s: %s (%s)", key, message, details)
        else:
            logging.getLogger("sari.http_server").warning("%s: %s", key, message)

    def _status_warning_counts_json(self):
        if not hasattr(self, "_status_warning_counts"):
            return {}
        return {str(k): int(v or 0) for k, v in self._status_warning_counts.items()}

    @staticmethod
    def _parse_log_line_ts(text: str) -> float:
        raw = str(text or "")
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{1,6})?)", raw)
        if not m:
            return 0.0
        token = m.group(1)
        for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return _dt.datetime.strptime(token, fmt).timestamp()
            except Exception:
                continue
        return 0.0

    def _read_recent_log_error_entries(self, limit: int = 50) -> list[dict[str, object]]:
        try:
            from sari.core.workspace import WorkspaceManager
            env_log_dir = os.environ.get("SARI_LOG_DIR")
            log_dir = os.path.expanduser(env_log_dir) if env_log_dir else str(WorkspaceManager.get_global_log_dir())
            log_file = os.path.join(log_dir, "daemon.log")
            if not os.path.exists(log_file):
                return []
            file_size = os.path.getsize(log_file)
            read_size = min(file_size, 1024 * 1024)
            with open(log_file, "rb") as f:
                if file_size > read_size:
                    f.seek(file_size - read_size)
                chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.splitlines()
            out: list[dict[str, object]] = []
            level_pat = re.compile(r"\b(ERROR|CRITICAL)\b")
            for line in reversed(lines):
                text = str(line or "").strip()
                if not text:
                    continue
                if level_pat.search(text):
                    out.append({"text": text, "ts": float(self._parse_log_line_ts(text) or 0.0)})
                if len(out) >= max(1, int(limit)):
                    break
            out.reverse()
            return out
        except Exception:
            return []

    def _build_errors_payload(
        self,
        limit: int = 50,
        source: str = "all",
        reason_codes: Optional[set[str]] = None,
        since_sec: int = 0,
    ):
        lim = max(1, min(int(limit or 50), 200))
        source_norm = str(source or "all").strip().lower()
        if source_norm not in {"all", "log", "warning"}:
            source_norm = "all"
        reason_filter = {str(rc).strip() for rc in (reason_codes or set()) if str(rc).strip()}
        since = max(0, int(since_sec or 0))
        cutoff_ts = time.time() - since if since > 0 else 0.0

        warnings_recent = warning_sink.warnings_recent()
        if isinstance(warnings_recent, list):
            filtered_warnings = []
            for item in warnings_recent:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("reason_code") or "")
                ts = float(item.get("ts") or 0.0)
                if reason_filter and code not in reason_filter:
                    continue
                if cutoff_ts > 0 and ts > 0 and ts < cutoff_ts:
                    continue
                filtered_warnings.append(item)
            warnings_recent = filtered_warnings
        else:
            warnings_recent = []

        log_entries = self._read_recent_log_error_entries(limit=lim)
        if cutoff_ts > 0:
            log_entries = [e for e in log_entries if float(e.get("ts") or 0.0) >= cutoff_ts]
        log_errors = [str(e.get("text") or "") for e in log_entries]
        if source_norm == "log":
            warnings_recent = []
        elif source_norm == "warning":
            log_entries = []
            log_errors = []
        return {
            "ok": True,
            "limit": lim,
            "source": source_norm,
            "reason_codes": sorted(list(reason_filter)),
            "since_sec": since,
            "warnings_recent": warnings_recent[-lim:] if isinstance(warnings_recent, list) else [],
            "warning_counts": warning_sink.warning_counts(),
            "status_warning_counts": self._status_warning_counts_json(),
            "log_errors": log_errors[-lim:],
            "log_error_entries": log_entries[-lim:],
        }

    @staticmethod
    def _indexer_workspace_roots(indexer) -> list[str]:
        cfg_obj = getattr(indexer, "cfg", None) or getattr(indexer, "config", None)
        roots = getattr(cfg_obj, "workspace_roots", []) if cfg_obj is not None else []
        return list(roots or [])

    def _get_db_storage_metrics(self, db_obj=None):
        try:
            db_ref = db_obj or self.db
            db_path = str(getattr(db_ref, "db_path", "") or "")
            if not db_path:
                return {
                    "db_size": 0,
                    "db_main_size": 0,
                    "db_wal_size": 0,
                    "db_shm_size": 0,
                    "db_metrics_ok": True,
                    "db_metrics_error_count": 0,
                }
            main_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
            wal_path = f"{db_path}-wal"
            shm_path = f"{db_path}-shm"
            wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            shm_size = os.path.getsize(shm_path) if os.path.exists(shm_path) else 0
            return {
                "db_size": int(main_size + wal_size + shm_size),
                "db_main_size": int(main_size),
                "db_wal_size": int(wal_size),
                "db_shm_size": int(shm_size),
                "db_metrics_ok": True,
                "db_metrics_error_count": 0,
            }
        except Exception as e:
            self._warn_status(
                "DB_STORAGE_METRICS_FAILED",
                "Failed to compute DB storage metrics",
                error=repr(e),
            )
            return {
                "db_size": 0,
                "db_main_size": 0,
                "db_wal_size": 0,
                "db_shm_size": 0,
                "db_metrics_ok": False,
                "db_metrics_error_count": 1,
            }

    def _get_db_size(self, db_obj=None):
        return int(self._get_db_storage_metrics(db_obj).get("db_size", 0))

    @staticmethod
    def _normalize_workspace_path(path: str) -> str:
        if not path:
            return ""
        expanded = os.path.expanduser(str(path))
        try:
            from sari.core.workspace import WorkspaceManager
            return WorkspaceManager.normalize_path(expanded)
        except Exception:
            return expanded.replace("\\", "/").rstrip("/")

    def _normalize_workspace_path_with_meta(self, path: str):
        if not path:
            return "", "empty"
        expanded = os.path.expanduser(str(path))
        try:
            from sari.core.workspace import WorkspaceManager
            return WorkspaceManager.normalize_path(expanded), "workspace_manager"
        except Exception as e:
            self._warn_status(
                "WORKSPACE_NORMALIZE_FAILED",
                "Workspace path normalization failed; using fallback",
                path=expanded,
                error=repr(e),
            )
            return expanded.replace("\\", "/").rstrip("/"), "fallback"

    def _selected_workspace_root(self, qs) -> str:
        sel = ""
        try:
            vals = qs.get("workspace_root") or []
            if vals:
                sel = str(vals[0] or "").strip()
        except Exception as e:
            request_id = ""
            try:
                hdrs = getattr(self, "headers", None)
                request_id = str((hdrs.get("X-Request-ID") if hdrs is not None else "") or "").strip()
            except Exception:
                request_id = ""
            if not request_id:
                request_id = uuid7_hex()[:12]
            self._warn_status(
                "WORKSPACE_QUERY_PARSE_FAILED",
                "Failed to parse workspace_root query parameter",
                request_id=request_id,
                error=repr(e),
            )
            sel = ""
        if sel:
            try:
                from sari.core.workspace import WorkspaceManager
                return WorkspaceManager.normalize_path(sel)
            except Exception as e:
                self._warn_status(
                    "WORKSPACE_QUERY_NORMALIZE_FAILED",
                    "Failed to normalize selected workspace_root query parameter",
                    workspace_root=sel,
                    error=repr(e),
                )
                return sel
        return self.workspace_root

    def _resolve_runtime(self, qs):
        workspace_root = self._selected_workspace_root(qs)
        db = self.db
        indexer = self.indexer
        root_ids = self.root_ids
        registry_resolve_failed = False
        if not self.shared_http_gateway or not workspace_root:
            return workspace_root, db, indexer, root_ids, registry_resolve_failed
        try:
            from sari.mcp.workspace_registry import Registry
            from sari.core.workspace import WorkspaceManager
            state = Registry.get_instance().get_or_create(
                workspace_root, persistent=True, track_ref=False)
            db = state.db
            indexer = state.indexer
            roots = self._indexer_workspace_roots(indexer)
            root_ids = [WorkspaceManager.root_id_for_workspace(
                r) for r in roots] if roots else []
        except Exception as e:
            registry_resolve_failed = True
            self._warn_status(
                "REGISTRY_RESOLVE_FAILED",
                "Failed to resolve runtime workspace state from registry",
                workspace_root=workspace_root,
                error=repr(e),
            )
        return workspace_root, db, indexer, root_ids, registry_resolve_failed

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _registered_workspaces(self, workspace_root: str, db, indexer):
        configured_roots = []
        workspace_manager = None
        try:
            from sari.core.workspace import WorkspaceManager
            from sari.core.config.main import Config

            workspace_manager = WorkspaceManager
            base_root = workspace_root or WorkspaceManager.resolve_workspace_root()
            cfg_path = WorkspaceManager.resolve_config_path(base_root)
            cfg = Config.load(cfg_path, workspace_root_override=base_root)
            configured_roots = list(getattr(cfg, "workspace_roots", []) or [])
        except Exception:
            configured_roots = [workspace_root] if workspace_root else []

        norm_roots = []
        seen = set()
        normalized_by_path = {}
        normalize_fallback_count = 0
        for root in configured_roots:
            if not root:
                continue
            normalized, normalized_by = self._normalize_workspace_path_with_meta(str(root))
            if normalized and normalized not in seen:
                seen.add(normalized)
                norm_roots.append(normalized)
                normalized_by_path[normalized] = normalized_by
            if normalized_by == "fallback":
                normalize_fallback_count += 1

        indexed_by_path = {}
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
                        normalized, normalized_by = self._normalize_workspace_path_with_meta(str(p))
                        indexed_by_path[normalized] = row
                        if normalized_by == "fallback":
                            normalize_fallback_count += 1
                    except Exception as row_error:
                        row_parse_error_count += 1
                        self._warn_status(
                            "WORKSPACE_ROW_PARSE_FAILED",
                            "Failed to parse workspace root row",
                            error=repr(row_error),
                            raw_row=repr(row),
                        )
            except Exception as e:
                self._warn_status(
                    "WORKSPACE_ROOTS_FETCH_FAILED",
                    "Failed to load workspace roots from DB",
                    error=repr(e),
                )
        failed_by_root = {}
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
                    if isinstance(row, dict):
                        rid = str(row.get("root_id") or "")
                        pending_count = int(row.get("pending_count") or 0)
                        failed_count = int(row.get("failed_count") or 0)
                    else:
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
                    if rid:
                        failed_by_root[rid] = {
                            "pending_count": pending_count,
                            "failed_count": failed_count,
                        }
            except Exception as e:
                self._warn_status(
                    "FAILED_TASKS_AGGREGATE_FAILED",
                    "Failed to aggregate failed task counts",
                    error=repr(e),
                )

        items = []
        watched_roots = set()
        worker_alive = False
        pending_rescan = False
        try:
            cfg_roots = self._indexer_workspace_roots(indexer)
            for root in cfg_roots:
                normalized, normalized_by = self._normalize_workspace_path_with_meta(str(root))
                watched_roots.add(normalized)
                if normalized_by == "fallback":
                    normalize_fallback_count += 1
            proc = getattr(indexer, "_worker_proc", None)
            worker_alive = bool(proc and proc.is_alive())
            pending_rescan = bool(getattr(indexer, "_pending_rescan", False))
        except Exception as e:
            self._warn_status(
                "WATCHED_ROOTS_RESOLVE_FAILED",
                "Failed while resolving watched workspace roots",
                error=repr(e),
            )

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

            items.append({
                "path": root,
                "normalized_by": normalized_by_path.get(root, "workspace_manager"),
                "exists": bool(exists),
                "readable": bool(readable),
                "watched": bool(watched),
                "indexed": bool(indexed),
                "file_count": int((indexed_row or {}).get("file_count", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "last_indexed_ts": int((indexed_row or {}).get("last_indexed_ts", 0) or (indexed_row or {}).get("updated_ts", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "pending_count": int(failed_counts.get("pending_count", 0) or 0),
                "failed_count": int(failed_counts.get("failed_count", 0) or 0),
                "status": status,
                "reason": reason,
                "index_state": index_state,
                "root_id": computed_root_id,
            })
        return {
            "workspaces": items,
            "normalization": {"fallback_count": int(normalize_fallback_count)},
            "row_parse_error_count": int(row_parse_error_count),
            "status_warning_counts": self._status_warning_counts_json(),
        }

    def log_message(self, format, *args):
        # keep logs quiet
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            return self._serve_dashboard()

        ctx = {
            "method": "GET",
            "path": path,
            "qs": qs,
            "headers": dict(
                self.headers)}

        def _exec():
            # Try API first
            res = self._handle_get(path, qs)
            # If API returns 404, try serving static files
            if isinstance(res, dict) and res.get("status") == 404:
                if self._serve_static(path):
                    return {"ok": True, "status": 200, "__static__": True}
            return res

        resp = run_http_middlewares(ctx, self.middlewares, _exec)

        if isinstance(resp, dict) and resp.get("__static__"):
            return

        if isinstance(resp, dict):
            status = int(resp.pop("status", 200))
            return self._json(resp, status=status)
        return self._json(
            {"ok": False, "error": "invalid response"}, status=500)

    def _serve_dashboard(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self._get_dashboard_html().encode("utf-8"))

    def _get_dashboard_html(self):
        """Generate complete dashboard HTML."""
        return f"""
        <!DOCTYPE html>
        <html lang="en" class="dark">
        {self._get_dashboard_head()}
        <body class="p-6">
            <div id="root"></div>
            {self._get_dashboard_script()}
        </body>
        </html>
        """

    def _get_dashboard_head(self):
        """Generate HTML head section with styles and external dependencies."""
        return """
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Sari Dashboard</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
            <script src="https://cdn.tailwindcss.com"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
            <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
            <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
            <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
            <style>
                :root {
                    --bg: #0f1218;
                    --surface: #171c24;
                    --surface-2: #1d2430;
                    --border: #2a3240;
                    --text: #dbe2ea;
                    --muted: #95a2b2;
                    --accent: #4f8cff;
                    --good: #45b36b;
                    --warn: #e2aa3a;
                    --bad: #e05757;
                }
                body {
                    background: radial-gradient(1200px 700px at 0% 0%, #172130 0%, var(--bg) 45%) fixed;
                    color: var(--text);
                    font-family: 'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif;
                }
                .mono { font-family: 'IBM Plex Mono', ui-monospace, Menlo, Monaco, Consolas, monospace; }
                .panel {
                    background: linear-gradient(180deg, var(--surface) 0%, #141920 100%);
                    border: 1px solid var(--border);
                    border-radius: 14px;
                }
                .subtle-shadow { box-shadow: 0 8px 30px rgba(0, 0, 0, 0.22); }
                .btn-primary {
                    background: var(--accent);
                    color: white;
                    border-radius: 10px;
                    font-weight: 600;
                    transition: background-color 0.2s ease;
                }
                .btn-primary:hover { background: #3d79eb; }
                .badge {
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;
                    padding: 3px 9px;
                    border: 1px solid var(--border);
                    border-radius: 999px;
                    font-size: 11px;
                    line-height: 1;
                    font-weight: 600;
                }
                .badge-indexed { color: var(--good); background: rgba(69, 179, 107, 0.12); border-color: rgba(69, 179, 107, 0.28); }
                .badge-missing { color: var(--bad); background: rgba(224, 87, 87, 0.12); border-color: rgba(224, 87, 87, 0.28); }
                .badge-registered { color: var(--accent); background: rgba(79, 140, 255, 0.12); border-color: rgba(79, 140, 255, 0.28); }
                .badge-watching { color: #7cc4ff; background: rgba(124, 196, 255, 0.12); border-color: rgba(124, 196, 255, 0.28); }
                .badge-stale { color: var(--warn); background: rgba(226, 170, 58, 0.12); border-color: rgba(226, 170, 58, 0.28); }
                .badge-blocked { color: #fca5a5; background: rgba(252, 165, 165, 0.12); border-color: rgba(252, 165, 165, 0.28); }
            </style>
        </head>
        """

    def _get_dashboard_script(self):
        """Generate React dashboard script."""
        return f"""
            <script type="text/babel">
                const {{ useState, useEffect }} = React;

                {self._get_react_components()}

                {self._get_dashboard_component()}

                const root = ReactDOM.createRoot(document.getElementById('root'));
                root.render(<Dashboard />);
            </script>
        """

    def _get_react_components(self):
        """Generate reusable React components (HealthMetric, StatCard)."""
        return """
                function HealthMetric({ label, percent, color }) {
                    return (
                        <div className="w-36">
                            <div className="flex justify-between text-[10px] text-gray-400 uppercase mb-1 tracking-wide">
                                <span>{label}</span>
                                <span>{Math.round(percent)}%</span>
                            </div>
                            <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden border border-slate-700">
                                <div className={`h-full ${color} transition-all duration-500`} style={{ width: `${Math.min(100, percent)}%` }}></div>
                            </div>
                        </div>
                    );
                }

                function StatCard({ icon, title, value, color, status, onClick }) {
                    const dotClass = status === "error" ? "bg-red-400" : status === "warn" ? "bg-amber-400" : status === "success" ? "bg-emerald-400" : "bg-slate-500";

                    return (
                        <div
                            className={`panel subtle-shadow p-5 ${onClick ? 'cursor-pointer hover:border-red-400/60 transition-colors' : ''}`}
                            onClick={onClick || undefined}
                        >
                            <div className="flex justify-between items-start mb-3">
                                <span className="text-[11px] text-slate-400 uppercase tracking-wide">{title}</span>
                                <div className={`w-8 h-8 rounded-md bg-slate-900/60 border border-slate-700 flex items-center justify-center ${color}`}>
                                    <i className={`fas ${icon}`}></i>
                                </div>
                            </div>
                            <div className="flex items-center justify-between">
                                <div className="text-2xl font-semibold text-slate-100 tracking-tight">{value}</div>
                                <div className={`w-2 h-2 rounded-full ${dotClass}`}></div>
                            </div>
                        </div>
                    );
                }
        """

    def _get_dashboard_component(self):
        """Generate main Dashboard React component."""
        return """
                function Dashboard() {
                    const [data, setData] = useState(null);
                    const [health, setHealth] = useState(null);
                    const [workspaces, setWorkspaces] = useState([]);
                    const [loading, setLoading] = useState(true);
                    const [rescanLoading, setRescanLoading] = useState(false);
                    const [errorPanelOpen, setErrorPanelOpen] = useState(false);
                    const [errorLoading, setErrorLoading] = useState(false);
                    const [errorDetails, setErrorDetails] = useState({ log_errors: [], warnings_recent: [] });
                    const [errorFilterSource, setErrorFilterSource] = useState('all');
                    const [errorReasonCode, setErrorReasonCode] = useState('');
                    const [errorSinceSec, setErrorSinceSec] = useState('');
                    const [copyNotice, setCopyNotice] = useState('');

                    const fetchData = async () => {
                        try {
                            const res = await fetch('/status');
                            const json = await res.json();
                            setData(json);
                            setLoading(false);
                        } catch (e) { console.error(e); }
                    };

                    const fetchHealth = async () => {
                        try {
                            const res = await fetch('/health-report');
                            const json = await res.json();
                            setHealth(json);
                        } catch (e) { console.error(e); }
                    };

                    const fetchWorkspaces = async () => {
                        try {
                            const res = await fetch('/workspaces');
                            const json = await res.json();
                            setWorkspaces(json.workspaces || []);
                        } catch (e) { console.error(e); }
                    };

                    const triggerRescan = async () => {
                        setRescanLoading(true);
                        try {
                            await fetch('/rescan', { method: 'GET' });
                            setTimeout(fetchData, 1000);
                        } catch (e) {
                            console.error(e);
                        } finally {
                            setRescanLoading(false);
                        }
                    };

                    const fetchErrors = async () => {
                        setErrorLoading(true);
                        try {
                            const params = new URLSearchParams();
                            params.set('limit', '80');
                            params.set('source', errorFilterSource || 'all');
                            if ((errorReasonCode || '').trim()) {
                                params.set('reason_code', (errorReasonCode || '').trim());
                            }
                            if ((errorSinceSec || '').trim()) {
                                params.set('since_sec', (errorSinceSec || '').trim());
                            }
                            const res = await fetch('/errors?' + params.toString());
                            const json = await res.json();
                            setErrorDetails(json || { log_errors: [], warnings_recent: [] });
                        } catch (e) {
                            console.error(e);
                            setErrorDetails({ log_errors: [], warnings_recent: [], error: String(e) });
                        } finally {
                            setErrorLoading(false);
                        }
                    };

                    const copyText = async (text) => {
                        try {
                            await navigator.clipboard.writeText(String(text || ''));
                            setCopyNotice('Copied');
                            setTimeout(() => setCopyNotice(''), 1200);
                        } catch (_e) {
                            setCopyNotice('Copy failed');
                            setTimeout(() => setCopyNotice(''), 1200);
                        }
                    };

                    useEffect(() => {
                        fetchData();
                        fetchHealth();
                        fetchWorkspaces();
                        const interval = setInterval(fetchData, 2000);
                        const wsInterval = setInterval(fetchWorkspaces, 5000);
                        const healthInterval = setInterval(fetchHealth, 30000);
                        return () => { clearInterval(interval); clearInterval(wsInterval); clearInterval(healthInterval); };
                    }, []);

                    if (!data) return <div className="flex items-center justify-center h-screen text-2xl animate-pulse text-blue-500 font-black">SARI LOADING...</div>;

                    const sys = data.system_metrics || {};
                    const errorCount = data.errors || 0;
                    const orphanWarnings = data.orphan_daemon_warnings || [];
                    const mergedWorkspaces = (data.workspaces && data.workspaces.length > 0)
                        ? data.workspaces
                        : workspaces;
                    const workspaceRows = (mergedWorkspaces && mergedWorkspaces.length > 0)
                        ? mergedWorkspaces
                        : (data.roots || []).map((root) => ({
                            path: root.path || root.root_path || "",
                            root_id: root.root_id || "",
                            file_count: root.file_count || 0,
                            last_indexed_ts: root.last_indexed_ts || root.updated_ts || 0,
                            pending_count: root.pending_count || 0,
                            failed_count: root.failed_count || 0,
                            readable: true,
                            watched: true,
                            status: (Number(root.file_count || 0) > 0 || Number(root.last_indexed_ts || 0) > 0) ? "indexed" : "registered",
                            reason: (Number(root.file_count || 0) > 0 || Number(root.last_indexed_ts || 0) > 0) ? "Indexed in DB" : "Registered but not indexed yet",
                            index_state: (Number(root.file_count || 0) > 0 || Number(root.last_indexed_ts || 0) > 0) ? "Idle" : "Initial Scan Pending",
                          }));
                    const queueDepths = data.queue_depths || {};
                    const queueEntries = Object.entries(queueDepths)
                        .filter(([_, v]) => Number.isFinite(Number(v)))
                        .sort((a, b) => Number(b[1]) - Number(a[1]));

                    return (
                        <div className="max-w-7xl mx-auto space-y-7">
                            <header className="panel subtle-shadow px-6 py-5 flex flex-col gap-4 lg:flex-row lg:justify-between lg:items-center">
                                <div className="min-w-0">
                                    <h1 className="text-3xl md:text-4xl font-semibold text-slate-100 tracking-tight flex items-center">
                                        <i className="fas fa-bolt mr-3 text-blue-400"></i> SARI Insight
                                    </h1>
                                    <p className="mono text-slate-400 mt-1 text-xs md:text-sm">v{data.version} · {data.host}:{data.port}</p>
                                </div>
                                <div className="flex items-center gap-5 flex-wrap">
                                    <div className="flex gap-4">
                                        <HealthMetric label="CPU" percent={sys.process_cpu_percent || 0} color="bg-blue-500" />
                                        <HealthMetric label="RAM" percent={sys.memory_percent || 0} color="bg-sky-500" />
                                    </div>
                                    <button
                                        onClick={triggerRescan}
                                        disabled={rescanLoading}
                                        className={`btn-primary px-4 py-2.5 text-sm mono flex items-center ${rescanLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                                    >
                                        <i className={`fas fa-sync-alt mr-2 ${rescanLoading ? 'fa-spin' : ''}`}></i>
                                        {rescanLoading ? 'Requesting...' : 'Rescan'}
                                    </button>
                                </div>
                            </header>

                            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                                <StatCard icon="fa-binoculars" title="Scanned" value={data.scanned_files.toLocaleString()} color="text-slate-300" />
                                <StatCard icon="fa-file-code" title="Indexed" value={data.indexed_files.toLocaleString()} color="text-blue-400" />
                                <StatCard icon="fa-project-diagram" title="Symbols" value={(data.repo_stats ? Object.values(data.repo_stats).reduce((a,b)=>a+b, 0) : 0).toLocaleString()} color="text-cyan-300" />
                                <StatCard icon="fa-database" title="Storage" value={(sys.db_size / 1024 / 1024).toFixed(2) + " MB"} color="text-slate-300" />
                                <StatCard icon="fa-clock" title="Uptime" value={Math.floor(sys.uptime / 60) + "m"} color="text-slate-300" />
                                <StatCard
                                    icon="fa-exclamation-triangle"
                                    title="Errors"
                                    value={errorCount.toLocaleString()}
                                    color={errorCount > 0 ? "text-red-400" : "text-slate-400"}
                                    status={errorCount > 0 ? "error" : "success"}
                                    onClick={() => { setErrorPanelOpen(true); fetchErrors(); }}
                                />
                            </div>
                            <div className="text-[11px] text-slate-500 mono mt-1">
                                ERRORS card shows runtime indexer errors. Log Health scans daemon log history, so counts can differ.
                            </div>

                            {errorPanelOpen && (
                                <div className="panel subtle-shadow p-5 border-red-500/40">
                                    <div className="flex items-center justify-between mb-3">
                                        <h2 className="text-lg font-semibold text-red-300 flex items-center">
                                            <i className="fas fa-circle-exclamation mr-2"></i> Error Details
                                        </h2>
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs mono text-slate-400">{copyNotice}</span>
                                            <button onClick={fetchErrors} className="text-xs mono px-2 py-1 bg-slate-900 border border-slate-700 rounded hover:border-red-400/60">
                                                Refresh
                                            </button>
                                            <button onClick={() => setErrorPanelOpen(false)} className="text-xs mono px-2 py-1 bg-slate-900 border border-slate-700 rounded hover:border-slate-400/60">
                                                Close
                                            </button>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-1 md:grid-cols-4 gap-2 mb-3">
                                        <select value={errorFilterSource} onChange={(e) => setErrorFilterSource(e.target.value)} className="text-xs mono bg-slate-900 border border-slate-700 rounded px-2 py-1">
                                            <option value="all">all</option>
                                            <option value="log">log</option>
                                            <option value="warning">warning</option>
                                        </select>
                                        <input value={errorReasonCode} onChange={(e) => setErrorReasonCode(e.target.value)} placeholder="reason_code (comma)" className="text-xs mono bg-slate-900 border border-slate-700 rounded px-2 py-1" />
                                        <input value={errorSinceSec} onChange={(e) => setErrorSinceSec(e.target.value)} placeholder="since_sec" className="text-xs mono bg-slate-900 border border-slate-700 rounded px-2 py-1" />
                                        <button onClick={fetchErrors} className="text-xs mono px-2 py-1 bg-slate-900 border border-slate-700 rounded hover:border-blue-400/60">Apply Filters</button>
                                    </div>
                                    {errorLoading ? (
                                        <div className="text-sm text-slate-400 mono">Loading...</div>
                                    ) : (
                                        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                                            <div>
                                                <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">Daemon Log Errors</div>
                                                <div className="max-h-64 overflow-auto border border-slate-800 rounded p-2 bg-slate-950/60 space-y-1">
                                                    {(errorDetails.log_errors || []).length === 0 ? (
                                                        <div className="text-xs text-slate-500 mono">No recent log errors</div>
                                                    ) : (
                                                        (errorDetails.log_errors || []).map((line, idx) => (
                                                            <div key={idx} className="text-xs text-red-200/90 mono break-all border-b border-slate-900 pb-1">
                                                                <div>{line}</div>
                                                                <button onClick={() => copyText(line)} className="mt-1 text-[10px] text-slate-400 hover:text-red-300">Copy</button>
                                                            </div>
                                                        ))
                                                    )}
                                                </div>
                                            </div>
                                            <div>
                                                <div className="text-xs uppercase tracking-wide text-slate-400 mb-2">Warnings Recent</div>
                                                <div className="max-h-64 overflow-auto border border-slate-800 rounded p-2 bg-slate-950/60 space-y-2">
                                                    {(errorDetails.warnings_recent || []).length === 0 ? (
                                                        <div className="text-xs text-slate-500 mono">No recent warnings</div>
                                                    ) : (
                                                        (errorDetails.warnings_recent || []).map((w, idx) => (
                                                            <div key={idx} className="text-xs">
                                                                <div className="text-amber-300 mono">{w.reason_code || 'UNKNOWN'}</div>
                                                                <div className="text-slate-400 mono">{w.where || ''}</div>
                                                                <div className="text-slate-300 break-all">{(w.extra && w.extra.message) ? String(w.extra.message) : ''}</div>
                                                                <button onClick={() => copyText(JSON.stringify(w, null, 2))} className="mt-1 text-[10px] text-slate-400 hover:text-amber-300">Copy</button>
                                                            </div>
                                                        ))
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {orphanWarnings.length > 0 && (
                                <div className="panel subtle-shadow p-4 border-red-500/40">
                                    <div className="flex items-center gap-2 text-red-300 font-semibold mb-2">
                                        <i className="fas fa-triangle-exclamation"></i>
                                        <span>Orphan Daemon Warning</span>
                                    </div>
                                    <div className="space-y-1 text-sm text-red-200/90 mono">
                                        {orphanWarnings.map((w, idx) => (
                                            <div key={idx} title={w} className="truncate">{w}</div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            <div className="panel subtle-shadow p-6">
                                <h2 className="text-xl font-semibold mb-5 flex items-center text-slate-100">
                                    <i className="fas fa-layer-group mr-3 text-blue-400"></i> System Queues
                                </h2>
                                {queueEntries.length > 0 ? (
                                    <div className="space-y-2">
                                        {queueEntries.map(([name, rawVal]) => {
                                            const val = Number(rawVal) || 0;
                                            const width = Math.min((val / 200) * 100, 100);
                                            const bar = val > 100 ? "bg-amber-400" : "bg-blue-500";
                                            return (
                                                <div key={name}>
                                                    <div className="flex items-center justify-between mono text-xs text-slate-300">
                                                        <span>{name}</span>
                                                        <span>{val.toLocaleString()}</span>
                                                    </div>
                                                    <div className="mt-1 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                                        <div className={`h-full ${bar}`} style={{ width: `${width}%` }}></div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <div className="text-xs text-slate-500 mono">No live queue data</div>
                                )}
                            </div>

                            <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
                                <div className="xl:col-span-2">
                                    <div className="panel subtle-shadow p-6">
                                        <h2 className="text-xl font-semibold mb-5 flex items-center text-slate-100">
                                            <i className="fas fa-server mr-3 text-blue-400"></i> Workspaces
                                        </h2>
                                        <p className="text-[11px] text-slate-500 mb-4">
                                            Retry Queue: auto-retry items (&lt;3 attempts), Permanent Failures: items that exceeded retry limit (>=3 attempts).
                                        </p>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-left text-sm">
                                                <thead className="text-slate-400 text-[11px] uppercase border-b border-slate-700/80 tracking-wide">
                                                    <tr>
                                                        <th className="pb-3 font-medium">Workspace Root</th>
                                                        <th className="pb-3 font-medium">Status</th>
                                                        <th className="pb-3 font-medium">Reason</th>
                                                        <th className="pb-3 font-medium">Last Indexed</th>
                                                        <th className="pb-3 font-medium text-right">Indexed Files</th>
                                                        <th className="pb-3 font-medium text-right">Retry Queue</th>
                                                        <th className="pb-3 font-medium text-right">Permanent Failures</th>
                                                        <th className="pb-3 font-medium text-right">Rescan</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-slate-800/80">
                                                    {workspaceRows.map((root, i) => (
                                                        <tr key={i} className="hover:bg-slate-800/30 transition-colors">
                                                            <td className="py-4">
                                                                <div className="mono text-[13px] text-slate-200">{root.path}</div>
                                                                <div className="mono text-[10px] text-slate-500 mt-1">{root.root_id}</div>
                                                            </td>
                                                            <td className="py-4">
                                                                <span className={`badge ${
                                                                    root.status === 'indexed' ? 'badge-indexed' :
                                                                    root.status === 'watching' ? 'badge-watching' :
                                                                    root.status === 'indexed_stale' ? 'badge-stale' :
                                                                    root.status === 'missing' ? 'badge-missing' :
                                                                    root.status === 'blocked' ? 'badge-blocked' : 'badge-registered'
                                                                }`}>
                                                                    {root.status === 'indexed' ? 'Indexed' :
                                                                     root.status === 'watching' ? 'Watching' :
                                                                     root.status === 'indexed_stale' ? 'Stale' :
                                                                     root.status === 'missing' ? 'Missing' :
                                                                     root.status === 'blocked' ? 'Blocked' : 'Registered'}
                                                                </span>
                                                                <div className="mt-1 text-[10px] text-slate-500 mono">{root.index_state || 'Unknown'}</div>
                                                            </td>
                                                            <td className="py-4 text-slate-400 text-[13px]">
                                                                {root.reason}
                                                            </td>
                                                            <td className="py-4 text-slate-400 text-[13px] mono">
                                                                {root.last_indexed_ts > 0 ? new Date(root.last_indexed_ts * 1000).toLocaleString() : 'N/A'}
                                                            </td>
                                                            <td className="py-4 text-right text-slate-200 mono">
                                                                {Number(root.file_count || 0).toLocaleString()}
                                                            </td>
                                                            <td className="py-4 text-right text-amber-300 mono">
                                                                {Number(root.pending_count || 0).toLocaleString()}
                                                            </td>
                                                            <td className="py-4 text-right text-red-400 mono">
                                                                {Number(root.failed_count || 0).toLocaleString()}
                                                            </td>
                                                            <td className="py-4 text-right">
                                                                <button onClick={triggerRescan} className="text-slate-500 hover:text-blue-400 transition-colors p-2 bg-slate-900/40 border border-slate-700 rounded-md">
                                                                    <i className="fas fa-rotate-right text-sm"></i>
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                </div>

                                <div>
                                    <div className="panel subtle-shadow p-6">
                                        <h2 className="text-xl font-semibold mb-5 flex items-center text-slate-100">
                                            <i className="fas fa-heartbeat mr-3 text-blue-400"></i> Health
                                        </h2>
                                        <div className="space-y-3">
                                            {health ? health.results.map((r, i) => {
                                                const rawDetail = (r.error ?? r.detail ?? "");
                                                const detailText = String(rawDetail || "").trim() || "Healthy";
                                                const titleText = `${r.name}: ${detailText}`;
                                                return (
                                                <div
                                                    key={i}
                                                    className="flex items-center justify-between border-b border-slate-800 pb-3 last:border-0 cursor-help"
                                                    title={titleText}
                                                >
                                                    <div>
                                                        <div className="text-sm font-medium text-slate-200 truncate max-w-[220px]" title={r.name}>{r.name}</div>
                                                        <div
                                                            className="text-[11px] text-slate-500 truncate max-w-[220px] cursor-help"
                                                            title={detailText}
                                                        >
                                                            {detailText}
                                                        </div>
                                                    </div>
                                                    <div>
                                                        {r.passed ? (
                                                            <span className="mono text-[11px] px-2 py-1 rounded bg-emerald-500/15 text-emerald-300">OK</span>
                                                        ) : (r.warn ? (
                                                            <span className="mono text-[11px] px-2 py-1 rounded bg-amber-500/15 text-amber-300">WARN</span>
                                                        ) : (
                                                            <span className="mono text-[11px] px-2 py-1 rounded bg-red-500/15 text-red-300">FAIL</span>
                                                        ))}
                                                    </div>
                                                </div>
                                                );
                                            }) : <div className="text-slate-500">Checking health...</div>}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <footer className="pt-2 text-center text-slate-500 text-[11px] mono">
                                Sari indexing dashboard
                            </footer>
                        </div>
                    );
                }
        """

    def _serve_static(self, path: str) -> bool:
        """Serve static files from the 'static' directory."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_root = os.path.join(current_dir, "static")

        if path == "/":
            path = "/index.html"

        file_path = os.path.abspath(
            os.path.join(
                static_root,
                path.lstrip("/")))

        if not file_path.startswith(os.path.abspath(static_root)):
            return False

        if os.path.exists(file_path) and os.path.isfile(file_path):
            try:
                self.send_response(200)
                ctype, _ = mimetypes.guess_type(file_path)
                if ctype == "text/html":
                    ctype = "text/html; charset=utf-8"
                self.send_header(
                    "Content-Type",
                    ctype or "application/octet-stream")

                with open(file_path, "rb") as f:
                    content = f.read()
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                return True
            except Exception:
                return False
        return False

    def _handle_get(self, path, qs):
        self._init_request_status()
        workspace_root, db, indexer, root_ids, registry_resolve_failed = self._resolve_runtime(qs)

        if path == "/health":
            return {"ok": True}

        if path == "/health-report":
            try:
                from sari.mcp.tools.doctor import execute_doctor
                roots = [workspace_root] if workspace_root else None
                res = execute_doctor({}, db=db, roots=roots)
                # execute_doctor returns a dict with 'content' which has 'text'
                # (JSON string)
                if isinstance(res, dict) and "content" in res:
                    text = res["content"][0]["text"]
                    # Skip the PACK1 header if it's there
                    if text.startswith("PACK1"):
                        lines = text.split("\n")
                        for line in lines:
                            if line.startswith("t:"):
                                return json.loads(line[2:])
                    return json.loads(text)
            except Exception as e:
                # Fallback to simple health check
                try:
                    from .health import SariDoctor
                    doc = SariDoctor(workspace_root=workspace_root or None)
                    doc.run_all()
                    return doc.get_summary()
                except Exception:
                    return {"ok": False, "error": str(e)}

        if path == "/status":
            daemon_status = load_daemon_runtime_status()
            st = indexer.status
            runtime_status = {}
            if hasattr(indexer, "get_runtime_status"):
                try:
                    raw_runtime = indexer.get_runtime_status()
                    if isinstance(raw_runtime, dict):
                        runtime_status = raw_runtime
                except Exception as e:
                    self._warn_status(
                        "INDEXER_RUNTIME_STATUS_FAILED",
                        "Failed to resolve indexer runtime status; using base status",
                        error=repr(e),
                    )
            repo_stats = db.get_repo_stats(
                root_ids=root_ids) if hasattr(
                db, "get_repo_stats") else {}
            total_db_files = sum(repo_stats.values()) if repo_stats else 0
            orphan_daemons = detect_orphan_daemons()
            orphan_daemon_warnings = [
                f"Orphan daemon PID {d.get('pid')} detected (not in registry)"
                for d in orphan_daemons
            ]
            performance = {}
            if hasattr(indexer, "get_performance_metrics"):
                try:
                    raw_perf = indexer.get_performance_metrics()
                    if isinstance(raw_perf, dict):
                        performance = raw_perf
                except Exception:
                    performance = {}

            queue_depths = {}
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
            if not queue_depths:
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

            workspaces_payload = self._registered_workspaces(workspace_root, db, indexer)
            workspaces = workspaces_payload.get("workspaces", [])

            # Fetch real system metrics
            metrics = get_system_metrics()
            metrics["uptime"] = int(time.time() - self.start_time)
            metrics.update(self._get_db_storage_metrics(db))

            return {
                "ok": True,
                "host": self.server_host,
                "port": self.server_port,
                "version": self.server_version,
                "index_ready": bool(runtime_status.get("index_ready", bool(st.index_ready))),
                "last_scan_ts": int(runtime_status.get("scan_finished_ts", getattr(st, "scan_finished_ts", 0)) or 0),
                "scanned_files": int(runtime_status.get("scanned_files", getattr(st, "scanned_files", 0)) or 0),
                "indexed_files": int(runtime_status.get("indexed_files", getattr(st, "indexed_files", 0)) or 0),
                "symbols_extracted": int(runtime_status.get("symbols_extracted", getattr(st, "symbols_extracted", 0)) or 0),
                "total_files_db": total_db_files,
                "errors": int(runtime_status.get("errors", getattr(st, "errors", 0)) or 0),
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
                "workspaces": workspaces,
                "normalization": workspaces_payload.get("normalization", {"fallback_count": 0}),
                "row_parse_error_count": int(workspaces_payload.get("row_parse_error_count", 0) or 0),
                "registry_resolve_failed": bool(registry_resolve_failed),
                "roots": db.get_roots() if hasattr(db, "get_roots") else [],
                "workspace_root": workspace_root,
                "system_metrics": metrics,
                "status_warning_counts": self._status_warning_counts_json(),
                "warning_counts": warning_sink.warning_counts(),
                "warnings_recent": warning_sink.warnings_recent(),
            }

        if path == "/errors":
            raw_limit = 50
            source = str((qs.get("source", ["all"])[0] or "all")).strip().lower()
            raw_reason_codes = str((qs.get("reason_code", [""])[0] or "")).strip()
            raw_since = "0"
            try:
                raw_since = str((qs.get("since_sec", ["0"])[0] or "0")).strip()
            except Exception:
                raw_since = "0"
            try:
                raw_limit = int((qs.get("limit", ["50"])[0] or "50"))
            except Exception:
                raw_limit = 50
            try:
                since_sec = int(raw_since or "0")
            except Exception:
                since_sec = 0
            reason_codes = {
                part.strip() for part in raw_reason_codes.split(",") if str(part or "").strip()
            } if raw_reason_codes else set()
            return self._build_errors_payload(
                limit=raw_limit,
                source=source,
                reason_codes=reason_codes,
                since_sec=since_sec,
            )

        if path == "/workspaces":
            workspaces_payload = self._registered_workspaces(workspace_root, db, indexer)
            workspaces = workspaces_payload.get("workspaces", [])
            return {
                "ok": True,
                "workspace_root": workspace_root,
                "count": len(workspaces),
                "normalization": workspaces_payload.get("normalization", {"fallback_count": 0}),
                "row_parse_error_count": int(workspaces_payload.get("row_parse_error_count", 0) or 0),
                "status_warning_counts": self._status_warning_counts_json(),
                "workspaces": workspaces,
            }

        if path == "/search":
            q = (qs.get("q", [""])[0] or "").strip()
            repo = (qs.get("repo", [""])[0] or "").strip() or None
            try:
                limit = int((qs.get("limit", ["20"])[0] or "20"))
            except ValueError:
                limit = 20
            if not q:
                return {"ok": False, "error": "missing q", "status": 400}
            try:
                snippet_lines = max(
                    1, min(int(indexer.cfg.snippet_max_lines), 20))
            except (ValueError, TypeError, AttributeError):
                snippet_lines = 3
            opts = SearchOptions(
                query=q,
                repo=repo,
                limit=max(1, min(limit, 50)),
                snippet_lines=snippet_lines,
                root_ids=root_ids,
                total_mode="exact",
            )
            try:
                hits, meta = db.search(opts)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"search failed: {e}",
                    "status": 500}
            return {
                "ok": True,
                "q": q,
                "repo": repo,
                "meta": meta,
                "hits": [getattr(h, "__dict__", h) for h in hits],
            }

        if path == "/rescan":
            # Trigger a scan ASAP (non-blocking)
            indexer.status.index_ready = False
            if hasattr(indexer, "request_rescan"):
                try:
                    indexer.request_rescan()
                except Exception:
                    pass
            return {"ok": True, "requested": True}

        return {"ok": False, "error": "not found", "status": 404}

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path != "/mcp":
            return self._json({"ok": False, "error": "not found"}, status=404)

        if self.mcp_server is None:
            return self._json(
                {"ok": False, "error": "MCP disabled"}, status=503)

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(
                {"ok": False, "error": "invalid json"}, status=400)

        def _handle_one(req):
            return self.mcp_server.handle_request(req)

        response = _handle_one(payload)
        return self._json(response)


class DualStackServer(ThreadingHTTPServer):
    def server_bind(self):
        import socket
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def serve_forever(
    host: str,
    port: int,
    db: LocalSearchDB,
    indexer: Indexer,
    version: str = "dev",
    workspace_root: str = "",
    cfg=None,
    mcp_server=None,
    shared_http_gateway: bool = False,
) -> tuple:
    import socket
    socket.AF_INET6 if ":" in host or host.lower() == "localhost" else socket.AF_INET

    class BoundHandler(Handler):
        pass

    BoundHandler.db = db
    BoundHandler.indexer = indexer
    BoundHandler.server_host = host
    BoundHandler.server_version = version
    BoundHandler.mcp_server = mcp_server
    BoundHandler.workspace_root = workspace_root
    BoundHandler.shared_http_gateway = bool(shared_http_gateway)
    try:
        from sari.core.workspace import WorkspaceManager
        BoundHandler.root_ids = [WorkspaceManager.root_id_for_workspace(
            r) for r in indexer.cfg.workspace_roots]
    except Exception:
        BoundHandler.root_ids = []

    if port is None:
        port = 0
    try:
        httpd = DualStackServer((host, port), BoundHandler)
    except OSError as e:
        # Address already in use -> retry with ephemeral port.
        if getattr(e, "errno", None) in (48, 98, 10048) and port != 0:
            httpd = DualStackServer((host, 0), BoundHandler)
        else:
            raise
    actual_port = httpd.server_address[1]
    BoundHandler.server_port = actual_port

    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return (httpd, actual_port)
