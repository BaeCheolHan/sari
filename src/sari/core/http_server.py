import json
import os
import threading
import mimetypes
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from sari.version import __version__

# Support script mode and package mode
try:
    from .db import LocalSearchDB  # type: ignore
    from .indexer import Indexer  # type: ignore
    from .models import SearchOptions  # type: ignore
    from .http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from .utils.system import get_system_metrics # type: ignore
except ImportError:
    from db import LocalSearchDB  # type: ignore
    from indexer import Indexer  # type: ignore
    from models import SearchOptions  # type: ignore
    from http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from utils.system import get_system_metrics # type: ignore


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

    def _get_db_size(self):
        try:
            if hasattr(self.db, "db_path"):
                return os.path.getsize(self.db.db_path)
            return 0
        except: return 0

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # keep logs quiet
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            return self._serve_dashboard()

        ctx = {"method": "GET", "path": path, "qs": qs, "headers": dict(self.headers)}
        
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
        return self._json({"ok": False, "error": "invalid response"}, status=500)

    def _serve_dashboard(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self._get_dashboard_html().encode("utf-8"))

    def _get_dashboard_html(self):
        return """
        <!DOCTYPE html>
        <html lang="en" class="dark">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Sari Dashboard</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
            <style>
                body { background-color: #0b0e14; color: #d8d9da; font-family: 'Inter', ui-sans-serif, system-ui; }
                .grafana-card { background-color: #181b1f; border-left: 4px solid #3274d9; transition: transform 0.2s; }
                .grafana-card:hover { transform: translateY(-2px); }
                .btn-primary { background-color: #3274d9; transition: all 0.2s; }
                .btn-primary:hover { background-color: #1f60c4; }
                .scan-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
                @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }
                .bg-health { background: linear-gradient(90deg, #3274d9 0%, #1f60c4 100%); }
            </style>
        </head>
        <body class="p-6">
            <div id="root"></div>
            <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
            <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
            <script src="https://unpkg.com/babel-standalone@6/babel.min.js"></script>
            
            <script type="text/babel">
                const { useState, useEffect } = React;

                function Dashboard() {
                    const [data, setData] = useState(null);
                    const [loading, setLoading] = useState(true);

                    const fetchData = async () => {
                        try {
                            const res = await fetch('/status');
                            const json = await res.json();
                            setData(json);
                            setLoading(false);
                        } catch (e) { console.error(e); }
                    };

                    const triggerRescan = async () => {
                        await fetch('/rescan', { method: 'GET' });
                        fetchData();
                    };

                    useEffect(() => {
                        fetchData();
                        const interval = setInterval(fetchData, 2000);
                        return () => clearInterval(interval);
                    }, []);

                    if (!data) return <div className="flex items-center justify-center h-screen text-2xl animate-pulse text-blue-500 font-black">SARI LOADING...</div>;

                    const sys = data.system_metrics || {};

                    return (
                        <div className="max-w-7xl mx-auto">
                            <header className="flex justify-between items-center mb-10 border-b border-gray-800 pb-6">
                                <div>
                                    <h1 className="text-4xl font-black text-white flex items-center tracking-tight">
                                        <i className="fas fa-bolt mr-3 text-blue-500"></i> SARI <span className="text-blue-500 ml-2 font-light italic">INSIGHT</span>
                                    </h1>
                                    <p className="text-gray-500 mt-1 font-mono text-sm uppercase tracking-tighter">Version {data.version} • {data.host}:{data.port}</p>
                                </div>
                                <div className="flex items-center space-x-8">
                                    <div className="flex space-x-6">
                                        <HealthMetric label="CPU" percent={sys.process_cpu_percent || 0} color="bg-blue-500" />
                                        <HealthMetric label="RAM" percent={sys.memory_percent || 0} color="bg-emerald-500" />
                                    </div>
                                    <button onClick={triggerRescan} className="btn-primary text-white px-6 py-2.5 rounded shadow-lg font-bold flex items-center uppercase text-sm tracking-wider">
                                        <i className="fas fa-sync-alt mr-2"></i> Rescan
                                    </button>
                                </div>
                            </header>

                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
                                <StatCard icon="fa-file-code" title="Indexed Files" value={data.indexed_files.toLocaleString()} color="text-blue-400" />
                                <StatCard icon="fa-project-diagram" title="Total Symbols" value={(data.repo_stats ? Object.values(data.repo_stats).reduce((a,b)=>a+b, 0) : 0).toLocaleString()} color="text-emerald-400" />
                                <StatCard icon="fa-database" title="Storage" value={(sys.db_size / 1024 / 1024).toFixed(2) + " MB"} color="text-purple-400" />
                                <StatCard icon="fa-clock" title="Uptime" value={Math.floor(sys.uptime / 60) + "m"} color="text-orange-400" />
                            </div>

                            <div className="grafana-card rounded-lg p-8 shadow-2xl">
                                <h2 className="text-2xl font-bold mb-6 flex items-center text-white">
                                    <i className="fas fa-server mr-3 text-blue-500"></i> Active Workspaces
                                </h2>
                                <div className="overflow-x-auto">
                                    <table className="w-full text-left">
                                        <thead className="text-gray-500 text-[10px] uppercase border-b border-gray-800 tracking-widest">
                                            <tr>
                                                <th className="pb-4 font-black">Workspace Root</th>
                                                <th className="pb-4 font-black">Status</th>
                                                <th className="pb-4 font-black">Last Sync</th>
                                                <th className="pb-4 font-black text-right">Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-gray-800/50">
                                            {data.roots.map((root, i) => (
                                                <tr key={i} className="hover:bg-gray-800/30 transition-colors group">
                                                    <td className="py-5">
                                                        <div className="font-mono text-sm text-blue-300">{root.path}</div>
                                                        <div className="text-[10px] text-gray-600 mt-1 uppercase font-bold">{root.root_id}</div>
                                                    </td>
                                                    <td className="py-5">
                                                        <span className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${data.index_ready ? 'bg-green-900/30 text-green-400 border border-green-800/50' : 'bg-blue-900/30 text-blue-400 border border-blue-800/50 scan-pulse'}`}>
                                                            {data.index_ready ? 'Synced' : 'Indexing'}
                                                        </span>
                                                    </td>
                                                    <td className="py-5 text-gray-400 text-sm font-mono">
                                                        {data.last_scan_ts > 0 ? new Date(data.last_scan_ts * 1000).toLocaleTimeString() : 'Pending...'}
                                                    </td>
                                                    <td className="py-5 text-right">
                                                        <button onClick={triggerRescan} className="text-gray-600 hover:text-blue-400 transition-colors p-2 bg-gray-800/50 rounded-lg group-hover:scale-110 transform">
                                                            <i className="fas fa-play-circle text-lg"></i>
                                                        </button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                            
                            <footer className="mt-12 text-center text-gray-600 text-[10px] font-mono uppercase tracking-widest">
                                Sari High-Performance Indexing Engine • Gemini CLI Optimized
                            </footer>
                        </div>
                    );
                }

                function StatCard({ icon, title, value, color }) {
                    return (
                        <div className="grafana-card rounded-lg p-6 shadow-xl">
                            <div className="flex justify-between items-start mb-4">
                                <span className="text-[10px] font-black text-gray-500 uppercase tracking-widest">{title}</span>
                                <div className={`w-8 h-8 rounded-full bg-gray-800/50 flex items-center justify-center ${color}`}>
                                    <i className={`fas ${icon}`}></i>
                                </div>
                            </div>
                            <div className="text-3xl font-black text-white tracking-tighter">{value}</div>
                        </div>
                    );
                }

                function HealthMetric({ label, percent, color }) {
                    return (
                        <div className="w-32">
                            <div className="flex justify-between text-[10px] font-black text-gray-500 uppercase mb-1">
                                <span>{label}</span>
                                <span>{Math.round(percent)}%</span>
                            </div>
                            <div className="h-1.5 w-full bg-gray-800 rounded-full overflow-hidden border border-gray-700">
                                <div 
                                    className={`h-full ${color} transition-all duration-500`} 
                                    style={{ width: `${Math.min(100, percent)}%` }}
                                ></div>
                            </div>
                        </div>
                    );
                }

                const root = ReactDOM.createRoot(document.getElementById('root'));
                root.render(<Dashboard />);
            </script>
        </body>
        </html>
        """

    def _serve_static(self, path: str) -> bool:
        """Serve static files from the 'static' directory."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_root = os.path.join(current_dir, "static")
        
        if path == "/":
            path = "/index.html"
            
        file_path = os.path.abspath(os.path.join(static_root, path.lstrip("/")))
        
        if not file_path.startswith(os.path.abspath(static_root)):
            return False

        if os.path.exists(file_path) and os.path.isfile(file_path):
            try:
                self.send_response(200)
                ctype, _ = mimetypes.guess_type(file_path)
                if ctype == "text/html":
                    ctype = "text/html; charset=utf-8"
                self.send_header("Content-Type", ctype or "application/octet-stream")
                
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
        if path == "/health":
            return {"ok": True}

        if path == "/status":
            st = self.indexer.status
            repo_stats = self.db.get_repo_stats(root_ids=self.root_ids) if hasattr(self.db, "get_repo_stats") else {}
            total_db_files = sum(repo_stats.values()) if repo_stats else 0
            
            # Fetch real system metrics
            metrics = get_system_metrics()
            metrics["uptime"] = int(time.time() - self.start_time)
            metrics["db_size"] = self._get_db_size()

            return {
                "ok": True,
                "host": self.server_host,
                "port": self.server_port,
                "version": self.server_version,
                "index_ready": bool(st.index_ready),
                "last_scan_ts": st.scan_finished_ts,
                "scanned_files": getattr(st, "scanned_files", 0),
                "indexed_files": st.indexed_files,
                "total_files_db": total_db_files,
                "errors": getattr(st, "errors", 0),
                "repo_stats": repo_stats,
                "roots": self.db.get_roots() if hasattr(self.db, "get_roots") else [],
                "system_metrics": metrics
            }

        if path == "/rescan":
            # Trigger a scan ASAP (non-blocking)
            self.indexer.status.index_ready = False
            if hasattr(self.indexer, "request_rescan"):
                try:
                    self.indexer.request_rescan()
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
            return self._json({"ok": False, "error": "MCP disabled"}, status=503)

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json({"ok": False, "error": "invalid json"}, status=400)

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

def serve_forever(host: str, port: int, db: LocalSearchDB, indexer: Indexer, version: str = "dev", workspace_root: str = "", cfg=None, mcp_server=None) -> tuple:
    import socket
    import sys
    address_family = socket.AF_INET6 if ":" in host or host.lower() == "localhost" else socket.AF_INET

    class BoundHandler(Handler):
        pass

    BoundHandler.db = db
    BoundHandler.indexer = indexer
    BoundHandler.server_host = host
    BoundHandler.server_version = version
    BoundHandler.mcp_server = mcp_server
    try:
        from sari.core.workspace import WorkspaceManager
        BoundHandler.root_ids = [WorkspaceManager.root_id_for_workspace(r) for r in indexer.cfg.workspace_roots]
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
