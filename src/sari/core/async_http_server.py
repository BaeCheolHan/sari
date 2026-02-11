"""
Starlette 기반 비동기 HTTP 서버.

기존 ThreadingHTTPServer를 대체하는 현대적인 ASGI 구현.
환경변수 SARI_HTTP_ASYNC=true로 활성화.
"""
import json
import os
import asyncio
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

JsonObject: TypeAlias = dict[str, object]
JsonArray: TypeAlias = list[JsonObject]


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
            return metrics
        except Exception:
            return {}

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
            }
        except Exception:
            return {
                "db_size": 0,
                "db_main_size": 0,
                "db_wal_size": 0,
                "db_shm_size": 0,
            }

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
        # Shutdown - cleanup resources if needed
        pass
    
    async def health(self, request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"ok": True})

    async def dashboard(self, request: Request) -> HTMLResponse:
        """Serve dashboard HTML aligned with sync server."""
        from sari.core.http_server import Handler

        handler = Handler.__new__(Handler)
        html = handler._get_dashboard_html()
        return HTMLResponse(html, status_code=200)
    
    async def status(self, request: Request) -> JSONResponse:
        """Server status endpoint."""
        st = self.indexer.status
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
            "index_ready": bool(st.index_ready),
            "last_scan_ts": st.last_scan_ts,
            "last_commit_ts": self.indexer.get_last_commit_ts() if hasattr(self.indexer, "get_last_commit_ts") else 0,
            "scanned_files": st.scanned_files,
            "indexed_files": st.indexed_files,
            "total_files_db": total_db_files,
            "errors": st.errors,
            "orphan_daemon_count": len(orphan_daemons),
            "orphan_daemon_warnings": orphan_daemon_warnings,
            "fts_enabled": self.db.fts_enabled,
            "worker_count": getattr(self.indexer, "max_workers", 0),
            "performance": self.indexer.get_performance_metrics() if hasattr(self.indexer, "get_performance_metrics") else {},
            "queue_depths": self.indexer.get_queue_depths() if hasattr(self.indexer, "get_queue_depths") else {},
            "repo_stats": repo_stats,
            "roots": self.db.get_roots() if hasattr(self.db, "get_roots") else [],
            "system_metrics": self._get_system_metrics(),
        })
    
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
            hits, meta = await loop.run_in_executor(None, lambda: self.db.search_v2(opts))
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
        configured_roots: list[str] = []
        workspace_manager = None
        try:
            from sari.core.workspace import WorkspaceManager
            from sari.core.config.main import Config

            workspace_manager = WorkspaceManager
            base_root = self.workspace_root or WorkspaceManager.resolve_workspace_root()
            cfg_path = WorkspaceManager.resolve_config_path(base_root)
            cfg = Config.load(cfg_path, workspace_root_override=base_root)
            configured_roots = list(getattr(cfg, "workspace_roots", []) or [])
        except Exception:
            configured_roots = [self.workspace_root] if self.workspace_root else []

        norm_roots: list[str] = []
        seen: set[str] = set()
        for root in configured_roots:
            if not root:
                continue
            normalized = self._normalize_workspace_path(str(root))
            if normalized and normalized not in seen:
                seen.add(normalized)
                norm_roots.append(normalized)

        indexed_by_path: dict[str, JsonObject] = {}
        if hasattr(self.db, "get_roots"):
            try:
                rows = self.db.get_roots() or []
                for row in rows:
                    if isinstance(row, dict):
                        p = row.get("path") or row.get("root_path") or row.get("real_path")
                        if not p:
                            continue
                        normalized = self._normalize_workspace_path(str(p))
                        indexed_by_path[normalized] = row
            except Exception:
                pass
        failed_by_root: dict[str, JsonObject] = {}
        if hasattr(self.db, "execute"):
            try:
                failed_rows = self.db.execute(
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
            except Exception:
                pass

        watched_roots = set()
        try:
            cfg_roots = self._indexer_workspace_roots(self.indexer)
            for root in cfg_roots:
                watched_roots.add(self._normalize_workspace_path(str(root)))
        except Exception:
            pass

        workspaces: JsonArray = []
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
                reason = "Indexed in DB and watched"
                index_state = "Idle"
            elif indexed and not watched:
                status = "indexed_stale"
                reason = "Indexed in DB but not currently watched"
                index_state = "Stale"
            elif watched:
                status = "watching"
                reason = "Watching workspace, awaiting first index"
                index_state = "Initial Scan Pending"
            else:
                status = "registered"
                reason = "Configured but not currently watched"
                index_state = "Not Watching"

            workspaces.append({
                "path": root,
                "root_id": computed_root_id,
                "exists": bool(exists),
                "readable": bool(readable),
                "watched": bool(watched),
                "indexed": bool(indexed),
                "status": status,
                "reason": reason,
                "index_state": index_state,
                "file_count": int((indexed_row or {}).get("file_count", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "last_indexed_ts": int((indexed_row or {}).get("last_indexed_ts", 0) or (indexed_row or {}).get("updated_ts", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "pending_count": int(failed_counts.get("pending_count", 0) or 0),
                "failed_count": int(failed_counts.get("failed_count", 0) or 0),
            })

        return JSONResponse({
            "ok": True,
            "workspace_root": self.workspace_root,
            "count": len(workspaces),
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

    # Determine root_ids
    root_ids = []
    try:
        from sari.core.workspace import WorkspaceManager
        root_ids = [WorkspaceManager.root_id_for_workspace(r) for r in AsyncHttpServer._indexer_workspace_roots(indexer)]
    except Exception:
        pass
    
    # Create MCP server if not provided
    if mcp_server is None:
        try:
            from sari.mcp.server import LocalSearchMCPServer
            if cfg is not None:
                mcp_server = LocalSearchMCPServer(workspace_root, cfg=cfg, db=db, indexer=indexer)
            else:
                mcp_server = LocalSearchMCPServer(workspace_root)
        except Exception:
            pass
    
    # Find available port
    actual_port = port
    strategy = os.environ.get("SARI_HTTP_API_PORT_STRATEGY", "auto").strip().lower()
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.close()
    except OSError:
        if strategy == "strict":
            raise RuntimeError(f"HTTP API port {port} unavailable")
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
    
    shutdown_event = threading.Event()
    
    def run_server():
        try:
            asyncio.run(uvicorn_server.serve())
        except Exception:
            pass
        finally:
            shutdown_event.set()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    # Return compatible tuple (None for httpd, actual_port)
    # Note: For shutdown, set shutdown_event and uvicorn_server.should_exit = True
    return (uvicorn_server, actual_port)
