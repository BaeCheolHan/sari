"""Shared dashboard HTML rendering helpers for sync/async HTTP servers."""

def get_dashboard_html():
    """Generate complete dashboard HTML."""
    return f"""
    <!DOCTYPE html>
    <html lang="en" class="dark">
    {get_dashboard_head()}
    <body class="p-6">
        <div id="root"></div>
        {get_dashboard_script()}
    </body>
    </html>
    """

def get_dashboard_head():
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

def get_dashboard_script():
    """Generate React dashboard script."""
    return f"""
        <script type="text/babel">
            const {{ useState, useEffect }} = React;

            {get_react_components()}

            {get_dashboard_component()}

            const root = ReactDOM.createRoot(document.getElementById('root'));
            root.render(<Dashboard />);
        </script>
    """

def get_react_components():
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

def get_dashboard_component():
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
