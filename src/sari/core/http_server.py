import json
import os
import threading
import mimetypes
import time
import logging
from typing import Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from sari.version import __version__
from sari.core.warning_sink import warning_sink
from sari.core.utils.uuid7 import uuid7_hex
from sari.core.http_error_feed import (
    build_errors_payload as _build_errors_payload_impl,
    parse_log_line_ts as _parse_log_line_ts_impl,
    read_recent_log_error_entries as _read_recent_log_error_entries_impl,
)
from sari.core.http_workspace_feed import (
    build_registered_workspaces_payload as _build_registered_workspaces_payload_impl,
)
from sari.core.dashboard_html import (
    get_dashboard_component,
    get_dashboard_head,
    get_dashboard_html,
    get_dashboard_script,
    get_react_components,
)

# Support script mode and package mode
try:
    from .db import LocalSearchDB  # type: ignore
    from .indexer import Indexer  # type: ignore
    from .models import SearchOptions  # type: ignore
    from .http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from .utils.system import get_system_metrics  # type: ignore
    from .daemon_health import detect_orphan_daemons  # type: ignore
    from .policy_engine import load_daemon_runtime_status  # type: ignore
    from .workspace_state_registry import get_workspace_registry  # type: ignore
except ImportError:
    from db import LocalSearchDB  # type: ignore
    from indexer import Indexer  # type: ignore
    from models import SearchOptions  # type: ignore
    from http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from utils.system import get_system_metrics  # type: ignore
    from daemon_health import detect_orphan_daemons  # type: ignore
    from policy_engine import load_daemon_runtime_status  # type: ignore
    from workspace_state_registry import get_workspace_registry  # type: ignore


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
        return _parse_log_line_ts_impl(text)

    def _read_recent_log_error_entries(self, limit: int = 50) -> list[dict[str, object]]:
        return _read_recent_log_error_entries_impl(
            limit=limit,
            parse_ts=self._parse_log_line_ts,
        )

    def _build_errors_payload(
        self,
        limit: int = 50,
        source: str = "all",
        reason_codes: Optional[set[str]] = None,
        since_sec: int = 0,
    ):
        return _build_errors_payload_impl(
            limit=limit,
            source=source,
            reason_codes=reason_codes,
            since_sec=since_sec,
            warning_sink_obj=warning_sink,
            read_log_entries=self._read_recent_log_error_entries,
            status_warning_counts_provider=self._status_warning_counts_json,
        )

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
            from sari.core.workspace import WorkspaceManager
            # Guard against unbounded workspace accumulation from arbitrary
            # query parameters when shared gateway mode is enabled.
            allowed_roots = set()
            for raw_root in self._indexer_workspace_roots(self.indexer):
                try:
                    allowed_roots.add(WorkspaceManager.normalize_path(str(raw_root)))
                except Exception:
                    pass
            if allowed_roots and workspace_root not in allowed_roots:
                self._warn_status(
                    "WORKSPACE_NOT_REGISTERED",
                    "Requested workspace_root is not in configured roots; using default workspace.",
                    workspace_root=workspace_root,
                )
                workspace_root = self.workspace_root
            state = get_workspace_registry().get_or_create(
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
        worker_alive = False
        pending_rescan = False
        try:
            proc = getattr(indexer, "_worker_proc", None)
            worker_alive = bool(proc and proc.is_alive())
            pending_rescan = bool(getattr(indexer, "_pending_rescan", False))
        except Exception:
            worker_alive = False
            pending_rescan = False
        return _build_registered_workspaces_payload_impl(
            workspace_root=workspace_root,
            db=db,
            indexer=indexer,
            normalize_workspace_path_with_meta=self._normalize_workspace_path_with_meta,
            indexer_workspace_roots=self._indexer_workspace_roots,
            status_warning_counts_provider=self._status_warning_counts_json,
            warn_status=self._warn_status,
            worker_alive=worker_alive,
            pending_rescan=pending_rescan,
            watched_roots_warn_code="WATCHED_ROOTS_RESOLVE_FAILED",
            watched_roots_warn_message="Failed while resolving watched workspace roots",
        )

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
        return get_dashboard_html()

    def _get_dashboard_head(self):
        return get_dashboard_head()

    def _get_dashboard_script(self):
        return get_dashboard_script()

    def _get_react_components(self):
        return get_react_components()

    def _get_dashboard_component(self):
        return get_dashboard_component()

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
                from .health import SariDoctor
                doc = SariDoctor(workspace_root=workspace_root or None)
                doc.run_all()
                return doc.get_summary()
            except Exception as e:
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
