import json
import os
import threading
import mimetypes
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from sari.version import __version__

try:
    from .db import LocalSearchDB
    from .indexer import Indexer
    from .models import SearchOptions
    from .http_middleware import run_http_middlewares, default_http_middlewares
    from .utils.system import get_system_metrics
except ImportError:
    from db import LocalSearchDB
    from indexer import Indexer
    from models import SearchOptions
    from http_middleware import run_http_middlewares, default_http_middlewares
    from utils.system import get_system_metrics

class Handler(BaseHTTPRequestHandler):
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
            return os.path.getsize(self.db.db_path) if hasattr(self.db, "db_path") else 0
        except: return 0

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args): return

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path == "/": return self._serve_dashboard()
        ctx = {"method": "GET", "path": path, "qs": qs, "headers": dict(self.headers)}
        def _exec():
            res = self._handle_get(path, qs)
            if isinstance(res, dict) and res.get("status") == 404:
                if self._serve_static(path): return {"ok": True, "status": 200, "__static__": True}
            return res
        resp = run_http_middlewares(ctx, self.middlewares, _exec)
        if isinstance(resp, dict) and resp.get("__static__"): return
        if isinstance(resp, dict):
            status = int(resp.pop("status", 200))
            return self._json(resp, status=status)
        return self._json({"ok": False, "error": "invalid response"}, status=500)

    def _handle_get(self, path, qs):
        if path == "/health": return {"ok": True}
        
        # 1. RESTORED: Full status API with metrics
        if path == "/status":
            st = self.indexer.status
            repo_stats = self.db.get_repo_stats(root_ids=self.root_ids)
            sys_m = get_system_metrics()
            sys_m["uptime"] = int(time.time() - self.start_time)
            sys_m["db_size"] = self._get_db_size()
            return {
                "ok": True, "version": self.server_version, "index_ready": bool(st.index_ready),
                "last_scan_ts": st.scan_finished_ts, "scanned_files": st.scanned_files,
                "indexed_files": st.indexed_files, "repo_stats": repo_stats,
                "roots": self.db.get_roots(), "system_metrics": sys_m
            }

        # 2. RESTORED: Search API
        if path == "/search":
            q = (qs.get("q") or [""])[0].strip()
            if not q: return {"ok": False, "error": "missing q", "status": 400}
            opts = SearchOptions(query=q, limit=int((qs.get("limit") or ["20"])[0]))
            hits, meta = self.db.search_v2(opts)
            return {"ok": True, "hits": [h.__dict__ for h in hits], "meta": meta}

        # 3. RESTORED: Doctor, Graph, Candidates
        if path == "/doctor":
            from sari.core.health import SariDoctor
            doc = SariDoctor(); doc.run_all()
            return {"ok": True, "summary": doc.get_summary()}
        if path == "/repo-candidates":
            q = (qs.get("q") or [""])[0].strip()
            return {"ok": True, "candidates": self.db.repo_candidates(q=q)}
        if path == "/rescan":
            self.indexer.status.index_ready = False
            return {"ok": True, "requested": True}

        return {"ok": False, "error": "not found", "status": 404}

    def _serve_dashboard(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self._get_dashboard_html().encode("utf-8"))

    def _get_dashboard_html(self):
        # ... (Dashboard HTML code kept as previously implemented) ...
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
                .scan-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) lifestyle infinite; }
                @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }
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
                    const fetchData = async () => { try { const res = await fetch('/status'); setData(await res.json()); } catch(e){} };
                    useEffect(() => { fetchData(); const i = setInterval(fetchData, 2000); return () => clearInterval(i); }, []);
                    if (!data) return <div className="flex items-center justify-center h-screen text-blue-500 font-black">LOADING SARI...</div>;
                    const sys = data.system_metrics || {};
                    return (
                        <div className="max-w-7xl mx-auto">
                            <header className="flex justify-between items-center mb-10 border-b border-gray-800 pb-6">
                                <h1 className="text-4xl font-black text-white italic"><i className="fas fa-bolt text-blue-500 mr-2"></i> SARI INSIGHT</h1>
                                <div className="flex items-center space-x-8">
                                    <div className="flex space-x-4">
                                        <div className="text-xs font-bold text-gray-500 uppercase">CPU {Math.round(sys.cpu_percent)}%</div>
                                        <div className="text-xs font-bold text-gray-500 uppercase">RAM {Math.round(sys.memory_percent)}%</div>
                                    </div>
                                    <button onClick={() => fetch('/rescan')} className="btn-primary text-white px-6 py-2 rounded font-bold uppercase text-sm">Rescan</button>
                                </div>
                            </header>
                            <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
                                <div className="grafana-card rounded-lg p-6"><div className="text-gray-500 text-[10px] font-black uppercase">Files</div><div className="text-3xl font-black text-white">{data.indexed_files.toLocaleString()}</div></div>
                                <div className="grafana-card rounded-lg p-6"><div className="text-gray-500 text-[10px] font-black uppercase">Symbols</div><div className="text-3xl font-black text-white">{(data.repo_stats ? Object.values(data.repo_stats).reduce((a,b)=>a+b,0) : 0).toLocaleString()}</div></div>
                                <div className="grafana-card rounded-lg p-6"><div className="text-gray-500 text-[10px] font-black uppercase">DB Size</div><div className="text-3xl font-black text-white">{(sys.db_size/1024/1024).toFixed(2)} MB</div></div>
                                <div className="grafana-card rounded-lg p-6"><div className="text-gray-500 text-[10px] font-black uppercase">Uptime</div><div className="text-3xl font-black text-white">{Math.floor(sys.uptime/60)}m</div></div>
                            </div>
                            <div className="grafana-card rounded-lg p-8">
                                <h2 className="text-2xl font-bold mb-6 text-white"><i className="fas fa-server text-blue-500 mr-2"></i> Active Workspaces</h2>
                                <table className="w-full text-left">
                                    <thead className="text-gray-500 text-[10px] uppercase border-b border-gray-800"><tr><th className="pb-4">Root Path</th><th className="pb-4">Status</th><th className="pb-4 text-right">Actions</th></tr></thead>
                                    <tbody className="divide-y divide-gray-800/50">
                                        {data.roots.map((r, i) => (
                                            <tr key={i} className="hover:bg-gray-800/30">
                                                <td className="py-5 font-mono text-sm text-blue-300">{r.root_path}</td>
                                                <td className="py-5"><span className="px-3 py-1 rounded-full text-[10px] font-black uppercase bg-green-900/30 text-green-400 border border-green-800/50">Synced</span></td>
                                                <td className="py-5 text-right"><button onClick={() => fetch('/rescan')} className="text-gray-600 hover:text-blue-400"><i className="fas fa-play-circle text-lg"></i></button></td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
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
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_root = os.path.join(current_dir, "static")
        file_path = os.path.abspath(os.path.join(static_root, path.lstrip("/")))
        if not file_path.startswith(os.path.abspath(static_root)): return False
        if os.path.exists(file_path) and os.path.isfile(file_path):
            try:
                self.send_response(200)
                ctype, _ = mimetypes.guess_type(file_path)
                self.send_header("Content-Type", ctype or "application/octet-stream")
                with open(file_path, "rb") as f: content = f.read()
                self.send_header("Content-Length", str(len(content))); self.end_headers()
                self.wfile.write(content)
                return True
            except: return False
        return False

def serve_forever(host: str, port: int, db: LocalSearchDB, indexer: Indexer, version: str = "dev", workspace_root: str = "", cfg=None, mcp_server=None) -> tuple:
    import socket
    class BoundHandler(Handler): pass
    BoundHandler.db, BoundHandler.indexer, BoundHandler.server_host, BoundHandler.server_version, BoundHandler.mcp_server = db, indexer, host, version, mcp_server
    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    actual_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, actual_port