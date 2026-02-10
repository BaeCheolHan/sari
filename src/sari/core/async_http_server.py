"""
Starlette 기반 비동기 HTTP 서버.

기존 ThreadingHTTPServer를 대체하는 현대적인 ASGI 구현.
환경변수 SARI_HTTP_ASYNC=true로 활성화.
"""
import json
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from sari.version import __version__


class AsyncHttpServer:
    """
    Starlette 기반 비동기 HTTP 서버.
    
    lifespan으로 DB/Indexer 연결 관리.
    """
    
    def __init__(
        self,
        db: Any,
        indexer: Any,
        host: str = "127.0.0.1",
        port: int = 47777,
        version: str = __version__,
        workspace_root: str = "",
        root_ids: Optional[List[str]] = None,
        mcp_server: Any = None,
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
    
    def _get_system_metrics(self) -> Dict[str, Any]:
        try:
            from sari.core.utils.system import get_system_metrics
            return get_system_metrics()
        except Exception:
            return {}
    
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
    
    async def status(self, request: Request) -> JSONResponse:
        """Server status endpoint."""
        st = self.indexer.status
        repo_stats = {}
        if hasattr(self.db, "get_repo_stats"):
            repo_stats = self.db.get_repo_stats(root_ids=self.root_ids)
        total_db_files = sum(repo_stats.values()) if repo_stats else 0
        
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
        
        return JSONResponse({
            "ok": True,
            "q": q,
            "repo": repo,
            "meta": meta,
            "engine": engine_mode,
            "index_version": index_version,
            "hits": [h.__dict__ for h in hits],
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
        
        def _handle_one(req: Any) -> Optional[Dict[str, Any]]:
            if not isinstance(req, dict):
                return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
            return self.mcp_server.handle_request(req)
        
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
    
    def create_app(self) -> Starlette:
        """Create and configure Starlette application."""
        # Static files directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_root = os.path.join(current_dir, "static")
        
        routes = [
            Route("/health", self.health, methods=["GET"]),
            Route("/status", self.status, methods=["GET"]),
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
    db: Any,
    indexer: Any,
    version: str = __version__,
    workspace_root: str = "",
    cfg: Any = None,
    mcp_server: Any = None,
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
        root_ids = [WorkspaceManager.root_id_for_workspace(r) for r in indexer.cfg.workspace_roots]
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
