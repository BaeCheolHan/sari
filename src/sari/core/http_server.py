import json
import os
import threading
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from sari.version import __version__

# Support script mode and package mode
try:
    from .db import LocalSearchDB  # type: ignore
    from .indexer import Indexer  # type: ignore
    from .models import SearchOptions  # type: ignore
    from .http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from .utils.system import get_system_metrics  # type: ignore
except ImportError:
    from db import LocalSearchDB  # type: ignore
    from indexer import Indexer  # type: ignore
    from models import SearchOptions  # type: ignore
    from http_middleware import run_http_middlewares, default_http_middlewares  # type: ignore
    from utils.system import get_system_metrics  # type: ignore


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

    def _get_db_size(self, db_obj=None):
        try:
            db_ref = db_obj or self.db
            if hasattr(db_ref, "db_path"):
                return os.path.getsize(db_ref.db_path)
            return 0
        except Exception:
            return 0

    def _selected_workspace_root(self, qs) -> str:
        sel = ""
        try:
            vals = qs.get("workspace_root") or []
            if vals:
                sel = str(vals[0] or "").strip()
        except Exception:
            sel = ""
        if sel:
            try:
                from sari.core.workspace import WorkspaceManager
                return WorkspaceManager.normalize_path(sel)
            except Exception:
                return sel
        return self.workspace_root

    def _resolve_runtime(self, qs):
        workspace_root = self._selected_workspace_root(qs)
        db = self.db
        indexer = self.indexer
        root_ids = self.root_ids
        if not self.shared_http_gateway or not workspace_root:
            return workspace_root, db, indexer, root_ids
        try:
            from sari.mcp.workspace_registry import Registry
            from sari.core.workspace import WorkspaceManager
            state = Registry.get_instance().get_or_create(
                workspace_root, persistent=True, track_ref=False)
            db = state.db
            indexer = state.indexer
            roots = getattr(
                indexer.cfg,
                "workspace_roots",
                []) if getattr(
                indexer,
                "cfg",
                None) else []
            root_ids = [WorkspaceManager.root_id_for_workspace(
                r) for r in roots] if roots else []
        except Exception:
            pass
        return workspace_root, db, indexer, root_ids

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
        for root in configured_roots:
            if not root:
                continue
            normalized = os.path.expanduser(str(root)).replace("\\", "/").rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                norm_roots.append(normalized)

        indexed_by_path = {}
        if hasattr(db, "get_roots"):
            try:
                rows = db.get_roots() or []
                for row in rows:
                    if isinstance(row, dict):
                        p = row.get("path") or row.get("root_path") or row.get("real_path")
                        if not p:
                            continue
                        normalized = str(p).replace("\\", "/").rstrip("/")
                        indexed_by_path[normalized] = row
            except Exception:
                pass
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
            except Exception:
                pass

        items = []
        watched_roots = set()
        try:
            cfg_roots = getattr(getattr(indexer, "cfg", None), "workspace_roots", []) or []
            for root in cfg_roots:
                watched_roots.add(str(root).replace("\\", "/").rstrip("/"))
        except Exception:
            pass

        for root in norm_roots:
            abs_path = os.path.expanduser(root)
            exists = os.path.isdir(abs_path)
            readable = os.access(abs_path, os.R_OK | os.X_OK) if exists else False
            watched = root in watched_roots
            indexed_row = indexed_by_path.get(root)
            indexed = indexed_row is not None
            computed_root_id = ""
            if isinstance(indexed_row, dict):
                computed_root_id = str(indexed_row.get("root_id", "") or "")
            if not computed_root_id and workspace_manager is not None:
                try:
                    computed_root_id = str(workspace_manager.root_id_for_workspace(root))
                except Exception:
                    computed_root_id = ""
            failed_counts = failed_by_root.get(computed_root_id, {})
            status = "indexed" if indexed else ("missing" if not exists else "registered")
            if indexed:
                reason = "Indexed in DB"
            elif not exists:
                reason = "Path does not exist"
            elif not readable:
                reason = "Path is not readable"
            elif not watched:
                reason = "Not currently watched by indexer"
            else:
                reason = "Registered but not indexed yet"

            items.append({
                "path": root,
                "exists": bool(exists),
                "readable": bool(readable),
                "watched": bool(watched),
                "indexed": bool(indexed),
                "file_count": int((indexed_row or {}).get("file_count", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "last_indexed_ts": int((indexed_row or {}).get("updated_ts", 0) or 0) if isinstance(indexed_row, dict) else 0,
                "pending_count": int(failed_counts.get("pending_count", 0) or 0),
                "failed_count": int(failed_counts.get("failed_count", 0) or 0),
                "status": status,
                "reason": reason,
                "root_id": computed_root_id,
            })
        return items

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

                function StatCard({ icon, title, value, color, status }) {
                    const dotClass = status === "error" ? "bg-red-400" : status === "warn" ? "bg-amber-400" : status === "success" ? "bg-emerald-400" : "bg-slate-500";

                    return (
                        <div className="panel subtle-shadow p-5">
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
                    const workspaceRows = (workspaces && workspaces.length > 0)
                        ? workspaces
                        : (data.roots || []).map((root) => ({
                            path: root.path || root.root_path || "",
                            root_id: root.root_id || "",
                            file_count: root.file_count || 0,
                            last_indexed_ts: root.last_indexed_ts || 0,
                            pending_count: root.pending_count || 0,
                            failed_count: root.failed_count || 0,
                            readable: true,
                            watched: true,
                            status: data.index_ready ? "indexed" : "registered",
                            reason: data.index_ready ? "Indexed in DB" : "Registered but not indexed yet",
                          }));

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
                                />
                            </div>

                            <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
                                <div className="xl:col-span-2">
                                    <div className="panel subtle-shadow p-6">
                                        <h2 className="text-xl font-semibold mb-5 flex items-center text-slate-100">
                                            <i className="fas fa-server mr-3 text-blue-400"></i> Workspaces
                                        </h2>
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-left text-sm">
                                                <thead className="text-slate-400 text-[11px] uppercase border-b border-slate-700/80 tracking-wide">
                                                    <tr>
                                                        <th className="pb-3 font-medium">Workspace Root</th>
                                                        <th className="pb-3 font-medium">Status</th>
                                                        <th className="pb-3 font-medium">Reason</th>
                                                        <th className="pb-3 font-medium">Last Indexed</th>
                                                        <th className="pb-3 font-medium text-right">Files</th>
                                                        <th className="pb-3 font-medium text-right">Pending</th>
                                                        <th className="pb-3 font-medium text-right">Failed</th>
                                                        <th className="pb-3 font-medium text-right">Actions</th>
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
                                                                    root.status === 'missing' ? 'badge-missing' : 'badge-registered'
                                                                }`}>
                                                                    {root.status === 'indexed' ? 'Synced' : (root.status === 'missing' ? 'Missing' : 'Registered')}
                                                                </span>
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
                                            {health ? health.results.map((r, i) => (
                                                <div key={i} className="flex items-center justify-between border-b border-slate-800 pb-3 last:border-0">
                                                    <div>
                                                        <div className="text-sm font-medium text-slate-200">{r.name}</div>
                                                        <div className="text-[11px] text-slate-500 truncate max-w-[220px]">{r.error || r.detail || 'Healthy'}</div>
                                                    </div>
                                                    <div>
                                                        {r.passed ?
                                                            <i className="fas fa-check-circle text-emerald-500"></i> :
                                                            (r.warn ? <i className="fas fa-exclamation-circle text-yellow-500"></i> : <i className="fas fa-times-circle text-red-500"></i>)
                                                        }
                                                    </div>
                                                </div>
                                            )) : <div className="text-slate-500">Checking health...</div>}
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
        workspace_root, db, indexer, root_ids = self._resolve_runtime(qs)

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
            st = indexer.status
            repo_stats = db.get_repo_stats(
                root_ids=root_ids) if hasattr(
                db, "get_repo_stats") else {}
            total_db_files = sum(repo_stats.values()) if repo_stats else 0

            # Fetch real system metrics
            metrics = get_system_metrics()
            metrics["uptime"] = int(time.time() - self.start_time)
            metrics["db_size"] = self._get_db_size(db)

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
                "roots": db.get_roots() if hasattr(db, "get_roots") else [],
                "workspace_root": workspace_root,
                "system_metrics": metrics
            }

        if path == "/workspaces":
            workspaces = self._registered_workspaces(workspace_root, db, indexer)
            return {
                "ok": True,
                "workspace_root": workspace_root,
                "count": len(workspaces),
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
                hits, meta = db.search_v2(opts)
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
