"""
Starlette 기반 비동기 HTTP 서버.

기존 ThreadingHTTPServer를 대체하는 현대적인 ASGI 구현.
환경변수 SARI_HTTP_ASYNC=true로 활성화.
"""
import json
import os
import asyncio
import inspect
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional, TypeAlias

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, HTMLResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from sari.version import __version__
from sari.core.daemon_health import detect_orphan_daemons
from sari.core.dashboard_html import get_dashboard_html
from sari.core.http_error_feed import (
    build_errors_payload as _build_errors_payload_impl,
    parse_log_line_ts as _parse_log_line_ts_impl,
    read_recent_log_error_entries as _read_recent_log_error_entries_impl,
)
from sari.core.http_workspace_feed import build_registered_workspaces_payload
from sari.core.mcp_runtime import create_mcp_server
from sari.core.policy_engine import load_daemon_runtime_status
from sari.core.warning_sink import warning_sink

JsonObject: TypeAlias = dict[str, object]


class AsyncHttpServer:
    """
    Starlette 기반 비동기 HTTP 서버.
    
    lifespan으로 DB/Indexer 연결 관리.
    """
    
    def __init__(
        self,
        db: object,
        indexer: object,
        host: str = "127.0.0.1",
        port: int = 47777,
        version: str = __version__,
        workspace_root: str = "",
        root_ids: Optional[list[str]] = None,
        mcp_server: object = None,
    ):
        self.db = db
        self.indexer = indexer
        self.host = host
        self.port = port
        self.version = version
        self.workspace_root = workspace_root
        self.root_ids = root_ids or []
        self.mcp_server = mcp_server
        self._app: Optional[Starlette] = None
        self._status_warning_counts: dict[str, int] = {}

    def _warn_status(self, code: str, message: str, **context: object) -> None:
        key = str(code or "UNKNOWN")
        self._status_warning_counts[key] = int(self._status_warning_counts.get(key, 0) or 0) + 1
        warning_sink.warn(
            reason_code=key,
            where="async_http_server",
            extra={"message": str(message), **dict(context)},
        )
        details = ", ".join(f"{k}={v!r}" for k, v in context.items())
        if details:
            logging.getLogger("sari.async_http_server").warning("%s: %s (%s)", key, message, details)
        else:
            logging.getLogger("sari.async_http_server").warning("%s: %s", key, message)

    def _warning_counts_json(self) -> JsonObject:
        return {str(k): int(v or 0) for k, v in self._status_warning_counts.items()}

    def _read_recent_log_errors(self, limit: int = 50) -> list[str]:
        entries = _read_recent_log_error_entries_impl(
            limit=limit,
            parse_ts=self._parse_log_line_ts,
        )
        return [str(item.get("text") or "") for item in entries]

    @staticmethod
    def _parse_log_line_ts(text: str) -> float:
        return _parse_log_line_ts_impl(text)

    def _read_recent_log_error_entries(self, limit: int = 50) -> list[JsonObject]:
        lines = self._read_recent_log_errors(limit=limit)
        return [
            {"text": str(line), "ts": float(self._parse_log_line_ts(str(line)) or 0.0)}
            for line in lines
        ]

    def _build_errors_payload(
        self,
        limit: int = 50,
        source: str = "all",
        reason_codes: Optional[set[str]] = None,
        since_sec: int = 0,
    ) -> JsonObject:
        return _build_errors_payload_impl(
            limit=limit,
            source=source,
            reason_codes=reason_codes,
            since_sec=since_sec,
            warning_sink_obj=warning_sink,
            read_log_entries=self._read_recent_log_error_entries,
            status_warning_counts_provider=self._warning_counts_json,
        )

    @staticmethod
    def _indexer_workspace_roots(indexer: object) -> list[str]:
        cfg_obj = getattr(indexer, "cfg", None) or getattr(indexer, "config", None)
        roots = getattr(cfg_obj, "workspace_roots", []) if cfg_obj is not None else []
        return list(roots or [])
    
    def _get_system_metrics(self) -> JsonObject:
        try:
            from sari.core.utils.system import get_system_metrics
            metrics = self._json_safe_metrics(get_system_metrics())
            metrics.update(self._get_db_storage_metrics())
            metrics["metrics_ok"] = True
            metrics.setdefault("metrics_error_count", 0)
            return metrics
        except Exception as e:
            self._warn_status(
                "SYSTEM_METRICS_FAILED",
                "Failed to fetch system metrics",
                error=repr(e),
            )
            return {
                "metrics_ok": False,
                "metrics_error_count": 1,
                "db_metrics_ok": False,
            }

    @staticmethod
    def _json_safe_metrics(metrics: object) -> JsonObject:
        """Return a JSON-serializable metrics dict with primitive leaves."""
        if not isinstance(metrics, dict):
            return {}
        out: JsonObject = {}
        for k, v in metrics.items():
            key = str(k)
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[key] = v
            elif isinstance(v, dict):
                out[key] = AsyncHttpServer._json_safe_metrics(v)
            elif isinstance(v, (list, tuple)):
                safe_items: list[object] = []
                for item in v:
                    if isinstance(item, (str, int, float, bool)) or item is None:
                        safe_items.append(item)
                out[key] = safe_items
            else:
                # Keep status API resilient when test/global monkeypatch injects MagicMock-like objects.
                out[key] = 0
        return out

    def _get_db_storage_metrics(self) -> JsonObject:
        try:
            db_path = str(getattr(self.db, "db_path", "") or "")
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

    def _normalize_workspace_path_with_meta(self, path: str) -> tuple[str, str]:
        if not path:
            return "", "empty"
        expanded = os.path.expanduser(str(path))
        try:
            from sari.core.workspace import WorkspaceManager
            return WorkspaceManager.normalize_path(expanded), "workspace_manager"
        except Exception as e:
            self._warn_status(
                "WORKSPACE_NORMALIZE_FAILED",
                "Workspace path normalization failed; using fallback normalization",
                path=expanded,
                error=repr(e),
            )
            return expanded.replace("\\", "/").rstrip("/"), "fallback"
    
    @asynccontextmanager
    async def lifespan(self, app: Starlette):
        """Startup/shutdown lifecycle management."""
        # Startup
        app.state.db = self.db
        app.state.indexer = self.indexer
        app.state.mcp_server = self.mcp_server
        app.state.root_ids = self.root_ids
        app.state.server_version = self.version
        app.state.server_host = self.host
        app.state.server_port = self.port
        yield
        await self._best_effort_close(getattr(app.state, "mcp_server", None), "app.state.mcp_server")
        await self._best_effort_close(getattr(app.state, "indexer", None), "app.state.indexer")
        await self._best_effort_close(getattr(app.state, "db", None), "app.state.db")

    async def _best_effort_close(self, obj: object, label: str) -> None:
        if obj is None:
            return
        for method_name in ("aclose", "close", "shutdown", "stop"):
            fn = getattr(obj, method_name, None)
            if not callable(fn):
                continue
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                self._warn_status(
                    "LIFESPAN_SHUTDOWN_FAILED",
                    "Failed during best-effort shutdown",
                    target=label,
                    method=method_name,
                    error=repr(e),
                )
            return
    
    async def health(self, request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"ok": True})

    async def dashboard(self, request: Request) -> HTMLResponse:
        """Serve dashboard HTML aligned with sync server."""
        return HTMLResponse(get_dashboard_html(), status_code=200)
    
    async def status(self, request: Request) -> JSONResponse:
        """Server status endpoint."""
        st = self.indexer.status
        runtime_status = {}
        if hasattr(self.indexer, "get_runtime_status"):
            try:
                raw_runtime = self.indexer.get_runtime_status()
                if isinstance(raw_runtime, dict):
                    runtime_status = raw_runtime
            except Exception as e:
                self._warn_status(
                    "INDEXER_RUNTIME_STATUS_FAILED",
                    "Failed to resolve indexer runtime status; using base status",
                    error=repr(e),
                )
        daemon_status = load_daemon_runtime_status()
        repo_stats = {}
        if hasattr(self.db, "get_repo_stats"):
            repo_stats = self.db.get_repo_stats(root_ids=self.root_ids)
        total_db_files = sum(repo_stats.values()) if repo_stats else 0
        orphan_daemons = detect_orphan_daemons()
        orphan_daemon_warnings = [
            f"Orphan daemon PID {d.get('pid')} detected (not in registry)"
            for d in orphan_daemons
        ]
        
        return JSONResponse({
            "ok": True,
            "host": self.host,
            "port": self.port,
            "version": self.version,
            "async_server": True,  # New flag to indicate async server
            "index_ready": bool(runtime_status.get("index_ready", bool(getattr(st, "index_ready", False)))),
            "last_scan_ts": int(runtime_status.get("scan_finished_ts", getattr(st, "scan_finished_ts", getattr(st, "last_scan_ts", 0))) or 0),
            "last_commit_ts": self.indexer.get_last_commit_ts() if hasattr(self.indexer, "get_last_commit_ts") else 0,
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
            "fts_enabled": self.db.fts_enabled,
            "worker_count": getattr(self.indexer, "max_workers", 0),
            "performance": self.indexer.get_performance_metrics() if hasattr(self.indexer, "get_performance_metrics") else {},
            "queue_depths": self.indexer.get_queue_depths() if hasattr(self.indexer, "get_queue_depths") else {},
            "repo_stats": repo_stats,
            "roots": self.db.get_roots() if hasattr(self.db, "get_roots") else [],
            "system_metrics": self._get_system_metrics(),
            "endpoint_ok": bool(getattr(self, "_endpoint_ok", True)),
            "status_warning_counts": self._warning_counts_json(),
            "warning_counts": warning_sink.warning_counts(),
            "warnings_recent": warning_sink.warnings_recent(),
        })

    async def errors(self, request: Request) -> JSONResponse:
        raw_limit = request.query_params.get("limit", "50")
        source = str(request.query_params.get("source", "all") or "all").strip().lower()
        raw_reason_codes = str(request.query_params.get("reason_code", "") or "").strip()
        raw_since = str(request.query_params.get("since_sec", "0") or "0").strip()
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 50
        try:
            since_sec = int(raw_since or "0")
        except Exception:
            since_sec = 0
        reason_codes = {
            part.strip() for part in raw_reason_codes.split(",") if str(part or "").strip()
        } if raw_reason_codes else set()
        return JSONResponse(
            self._build_errors_payload(
                limit=limit,
                source=source,
                reason_codes=reason_codes,
                since_sec=since_sec,
            )
        )
    
    async def search(self, request: Request) -> JSONResponse:
        """Search endpoint."""
        from sari.core.models import SearchOptions
        
        q = request.query_params.get("q", "").strip()
        repo = request.query_params.get("repo", "").strip() or None
        try:
            limit = int(request.query_params.get("limit", "20"))
        except ValueError:
            limit = 20
        
        if not q:
            return JSONResponse({"ok": False, "error": "missing q"}, status_code=400)
        
        # Determine engine mode
        engine = getattr(self.db, "engine", None)
        engine_mode = "sqlite"
        index_version = ""
        if engine and hasattr(engine, "status"):
            st = engine.status()
            engine_mode = st.engine_mode
            index_version = st.index_version
        
        try:
            snippet_lines = max(1, min(int(self.indexer.cfg.snippet_max_lines), 20))
        except (ValueError, TypeError, AttributeError):
            snippet_lines = 3
        
        opts = SearchOptions(
            query=q,
            repo=repo,
            limit=max(1, min(limit, 50)),
            snippet_lines=snippet_lines,
            root_ids=self.root_ids,
            total_mode="exact",
        )
        
        try:
            # Run in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()
            hits, meta = await loop.run_in_executor(None, lambda: self.db.search(opts))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"search failed: {e}"}, status_code=500)

        normalized_hits: list[JsonObject] = []
        for hit in hits:
            if isinstance(hit, dict):
                normalized_hits.append(dict(hit))
            else:
                normalized_hits.append(dict(getattr(hit, "__dict__", {})))
        
        return JSONResponse({
            "ok": True,
            "q": q,
            "repo": repo,
            "meta": meta,
            "engine": engine_mode,
            "index_version": index_version,
            "hits": normalized_hits,
        })
    
    async def rescan(self, request: Request) -> JSONResponse:
        """Trigger rescan endpoint."""
        self.indexer.request_rescan()
        return JSONResponse({"ok": True, "requested": True})
    
    async def repo_candidates(self, request: Request) -> JSONResponse:
        """Repo candidates endpoint."""
        q = request.query_params.get("q", "").strip()
        try:
            limit = int(request.query_params.get("limit", "3"))
        except ValueError:
            limit = 3
        
        if not q:
            return JSONResponse({"ok": False, "error": "missing q"}, status_code=400)
        
        cands = self.db.repo_candidates(q=q, limit=max(1, min(limit, 5)), root_ids=self.root_ids)
        return JSONResponse({"ok": True, "q": q, "candidates": cands})
    
    async def mcp_post(self, request: Request) -> Response:
        """MCP JSON-RPC over HTTP endpoint."""
        if self.mcp_server is None:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32000, "message": "MCP-over-HTTP is not enabled"},
                },
                status_code=503,
            )
        
        try:
            body = await request.body()
            if not body:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Empty request body"}},
                    status_code=400,
                )
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )
        
        def _handle_one(req: object) -> Optional[JsonObject]:
            if not isinstance(req, dict):
                return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
            resp = self.mcp_server.handle_request(req)
            return resp if isinstance(resp, dict) else None
        
        # Handle batch or single request
        loop = asyncio.get_running_loop()
        
        if isinstance(payload, list):
            responses = []
            for item in payload:
                resp = await loop.run_in_executor(None, _handle_one, item)
                if resp is not None:
                    responses.append(resp)
            if not responses:
                return Response(status_code=204)
            return JSONResponse(responses)
        
        response = await loop.run_in_executor(None, _handle_one, payload)
        if response is None:
            return Response(status_code=204)
        return JSONResponse(response)
    
    async def doctor(self, request: Request) -> JSONResponse:
        """Health doctor endpoint."""
        try:
            from sari.core.health import SariDoctor
            doc = SariDoctor(workspace_root=getattr(self.indexer, "workspace_root", None))
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, doc.run_all)
            return JSONResponse({"ok": True, "summary": doc.get_summary()})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    
    async def daemon_list(self, request: Request) -> JSONResponse:
        """List daemon processes."""
        try:
            from sari.core.utils.system import list_sari_processes
            return JSONResponse({"ok": True, "processes": list_sari_processes()})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    async def workspaces(self, request: Request) -> JSONResponse:
        """Registered workspace roots with health/indexing hints."""
        payload = build_registered_workspaces_payload(
            workspace_root=self.workspace_root,
            db=self.db,
            indexer=self.indexer,
            normalize_workspace_path_with_meta=self._normalize_workspace_path_with_meta,
            indexer_workspace_roots=self._indexer_workspace_roots,
            status_warning_counts_provider=self._warning_counts_json,
            warn_status=self._warn_status,
            watched_roots_warn_code="WATCHED_ROOTS_NORMALIZE_FAILED",
            watched_roots_warn_message="Failed while normalizing watched roots",
        )
        workspaces = payload.get("workspaces", [])

        return JSONResponse({
            "ok": True,
            "workspace_root": self.workspace_root,
            "count": len(workspaces),
            "normalization": payload.get("normalization", {"fallback_count": 0}),
            "row_parse_error_count": int(payload.get("row_parse_error_count", 0) or 0),
            "status_warning_counts": self._warning_counts_json(),
            "workspaces": workspaces,
        })
    
    def create_app(self) -> Starlette:
        """Create and configure Starlette application."""
        # Static files directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_root = os.path.join(current_dir, "static")
        
        routes = [
            Route("/", self.dashboard, methods=["GET"]),
            Route("/health", self.health, methods=["GET"]),
            Route("/status", self.status, methods=["GET"]),
            Route("/errors", self.errors, methods=["GET"]),
            Route("/workspaces", self.workspaces, methods=["GET"]),
            Route("/search", self.search, methods=["GET"]),
            Route("/rescan", self.rescan, methods=["GET"]),
            Route("/repo-candidates", self.repo_candidates, methods=["GET"]),
            Route("/doctor", self.doctor, methods=["GET"]),
            Route("/daemon/list", self.daemon_list, methods=["GET"]),
            Route("/mcp", self.mcp_post, methods=["POST"]),
        ]
        
        # Mount static files if directory exists
        if os.path.isdir(static_root):
            routes.append(Mount("/", app=StaticFiles(directory=static_root, html=True), name="static"))
        
        middleware = [
            Middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]),
        ]
        
        self._app = Starlette(
            debug=False,
            routes=routes,
            middleware=middleware,
            lifespan=self.lifespan,
        )
        
        return self._app


def serve_async(
    host: str,
    port: int,
    db: object,
    indexer: object,
    version: str = __version__,
    workspace_root: str = "",
    cfg: object = None,
    mcp_server: object = None,
) -> tuple:
    """
    Start async HTTP server with uvicorn.
    
    Returns:
        tuple: (server_task, actual_port, shutdown_event)
    """
    import uvicorn
    import socket
    import threading

    endpoint_ok = True
    endpoint_errors: list[str] = []

    # Determine root_ids
    root_ids = []
    try:
        from sari.core.workspace import WorkspaceManager
        root_ids = [WorkspaceManager.root_id_for_workspace(r) for r in AsyncHttpServer._indexer_workspace_roots(indexer)]
    except Exception:
        endpoint_ok = False
        endpoint_errors.append("ROOT_IDS_RESOLVE_FAILED")
        logging.getLogger("sari.async_http_server").warning(
            "Failed to resolve root_ids for async HTTP server",
            exc_info=True,
        )
    
    # Create MCP server if not provided
    if mcp_server is None:
        try:
            mcp_server = create_mcp_server(
                workspace_root=workspace_root,
                cfg=cfg,
                db=db,
                indexer=indexer,
            )
        except Exception:
            endpoint_ok = False
            endpoint_errors.append("MCP_SERVER_INIT_FAILED")
            logging.getLogger("sari.async_http_server").warning(
                "Failed to initialize MCP server for async HTTP server",
                exc_info=True,
            )
    
    # Find available port
    actual_port = port
    strategy = os.environ.get("SARI_HTTP_API_PORT_STRATEGY", "auto").strip().lower()
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.close()
    except OSError as err:
        if strategy == "strict":
            raise RuntimeError(f"HTTP API port {port} unavailable") from err
        # Auto-assign port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        actual_port = sock.getsockname()[1]
        sock.close()
    
    # Create server instance
    server = AsyncHttpServer(
        db=db,
        indexer=indexer,
        host=host,
        port=actual_port,
        version=version,
        workspace_root=workspace_root,
        root_ids=root_ids,
        mcp_server=mcp_server,
    )
    server._endpoint_ok = endpoint_ok
    for code in endpoint_errors:
        server._warn_status(code, "Async HTTP endpoint startup diagnostic failure")
    
    app = server.create_app()
    
    # Run uvicorn in a separate thread
    config = uvicorn.Config(
        app,
        host=host,
        port=actual_port,
        log_level="warning",
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.sari_endpoint_ok = endpoint_ok
    uvicorn_server.sari_endpoint_errors = list(endpoint_errors)
    
    shutdown_event = threading.Event()
    
    def run_server():
        try:
            asyncio.run(uvicorn_server.serve())
        except Exception:
            uvicorn_server.sari_endpoint_ok = False
            existing_errors = list(uvicorn_server.sari_endpoint_errors)
            existing_errors.append("ASYNC_SERVER_THREAD_FAILED")
            uvicorn_server.sari_endpoint_errors = existing_errors
            server._endpoint_ok = False
            server._warn_status(
                "ASYNC_SERVER_THREAD_FAILED",
                "Async HTTP server thread terminated with exception",
            )
            logging.getLogger("sari.async_http_server").warning(
                "Async HTTP server thread terminated with exception",
                exc_info=True,
            )
        finally:
            shutdown_event.set()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    # Return compatible tuple (None for httpd, actual_port)
    # Note: For shutdown, set shutdown_event and uvicorn_server.should_exit = True
    return (uvicorn_server, actual_port)
