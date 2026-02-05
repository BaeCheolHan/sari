import json
import os
import signal
import threading
import time
import ipaddress
from pathlib import Path
from datetime import datetime

# Support both `python3 app/main.py` (script mode) and package mode.
try:
    from .config import Config, resolve_config_path  # type: ignore
    from . import config as config_mod  # type: ignore
    from .db import LocalSearchDB  # type: ignore
    from .http_server import serve_forever  # type: ignore
    from .indexer import Indexer  # type: ignore
    from .workspace import WorkspaceManager  # type: ignore
except ImportError:  # script mode
    from config import Config, resolve_config_path  # type: ignore
    import config as config_mod  # type: ignore
    from db import LocalSearchDB  # type: ignore
    from http_server import serve_forever  # type: ignore
    from indexer import Indexer  # type: ignore
    from workspace import WorkspaceManager  # type: ignore


def _resolve_http_host(cfg_host: str, allow_non_loopback: bool) -> str:
    host = (cfg_host or "127.0.0.1").strip()
    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Non-IP hostnames are only allowed if they resolve to localhost explicitly.
        is_loopback = host.lower() == "localhost"
    if (not is_loopback) and (not allow_non_loopback):
        raise SystemExit(
            f"sari refused to start: server_host must be loopback only (127.0.0.1/localhost/::1). got={host}. "
            "Set SARI_ALLOW_NON_LOOPBACK=1 to override (NOT recommended)."
        )
    return host


def _resolve_version() -> str:
    try:
        from sari.version import __version__
        return __version__
    except Exception:
        return os.environ.get("SARI_VERSION", "dev")


def _create_mcp_server(workspace_root: str, cfg: Config, db: LocalSearchDB, indexer: Indexer):
    try:
        from sari.mcp.server import LocalSearchMCPServer
        return LocalSearchMCPServer(workspace_root, cfg=cfg, db=db, indexer=indexer)
    except Exception:
        return None


def _write_server_info(workspace_root: str, host: str, actual_port: int, config_port: int) -> None:
    data_dir = Path(workspace_root) / ".codex" / "tools" / "sari" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    server_json = data_dir / "server.json"
    server_info = {
        "host": host,
        "port": actual_port,  # use actual bound port, not config port
        "config_port": config_port,  # original requested port for reference
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(),
    }
    server_json.write_text(json.dumps(server_info, indent=2), encoding="utf-8")


def main() -> int:
    # Auto-detect workspace root for HTTP fallback
    workspace_root = WorkspaceManager.resolve_workspace_root()

    # Set env var so Config can pick it up
    os.environ["SARI_WORKSPACE_ROOT"] = workspace_root

    cfg_path = resolve_config_path(workspace_root)

    # Graceful config loading (Global Install Support)
    if os.path.exists(cfg_path):
        cfg = Config.load(cfg_path)
    else:
        # Use safe defaults if config.json is missing.
        print(f"[sari] Config not found in workspace ({cfg_path}), using defaults.")
        defaults = config_mod.Config.get_defaults(workspace_root)
        cfg = Config(**defaults)


    # Security hardening: loopback-only by default.
    # Allow opt-in override only when explicitly requested.
    allow_non_loopback = os.environ.get("SARI_ALLOW_NON_LOOPBACK") == "1"
    host = _resolve_http_host(cfg.http_api_host, allow_non_loopback)

    # Workspace-local DB path enforcement (multi-workspace support)
    # DB path is now determined by Config.load
    db_path = cfg.db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[sari] DB path: {db_path}")

    db = LocalSearchDB(db_path)
    try:
        from sari.core.engine_registry import get_default_engine
        db.set_engine(get_default_engine(db, cfg, cfg.workspace_roots))
    except Exception as e:
        print(f"[sari] engine init failed: {e}")
    from sari.core.indexer import resolve_indexer_settings
    mode, enabled, startup_enabled, lock_handle = resolve_indexer_settings(str(db_path))
    indexer = Indexer(cfg, db, indexer_mode=mode, indexing_enabled=enabled, startup_index_enabled=startup_enabled, lock_handle=lock_handle)

    # Start HTTP immediately so health checks don't block on initial indexing.
    # serve_forever returns (httpd, actual_port) for fallback tracking
    version = _resolve_version()
    mcp_server = _create_mcp_server(workspace_root, cfg, db, indexer)
    httpd, actual_port = serve_forever(
        host,
        cfg.http_api_port,
        db,
        indexer,
        version=version,
        workspace_root=workspace_root,
        cfg=cfg,
        mcp_server=mcp_server,
    )

    # Write server.json with actual binding info (single source of truth for port tracking)
    _write_server_info(workspace_root, host, actual_port, cfg.http_api_port)

    if actual_port != cfg.http_api_port:
        print(f"[sari] server.json updated with fallback port {actual_port}")

    try:
        port_file = Path(db_path + ".http_api.port")
        port_file.write_text(str(actual_port) + "\n", encoding="utf-8")
    except Exception:
        pass

    stop_evt = threading.Event()

    def _shutdown(*_):
        if stop_evt.is_set():
            return
        stop_evt.set()
        try:
            indexer.stop()
        except Exception:
            pass
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Index in background.
    idx_thread = threading.Thread(target=indexer.run_forever, daemon=True)
    idx_thread.start()

    try:
        while not stop_evt.is_set():
            time.sleep(0.2)
    finally:
        _shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
