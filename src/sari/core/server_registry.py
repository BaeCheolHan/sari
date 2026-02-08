import json
import os
import time
import socket
from pathlib import Path
from typing import Dict, Optional, Any, Iterable
from filelock import FileLock

# Backward compatibility for legacy tests/tools that patch module-level path.
REGISTRY_FILE = Path.home() / ".local" / os.path.join("share", "sari") / "server.json"
_FALLBACK_REGISTRY = Path(os.environ.get("SARI_REGISTRY_FALLBACK", "/tmp/sari/server.json"))

def _ensure_writable_dir(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.access(str(path.parent), os.W_OK)
    except Exception:
        return False

def get_registry_path() -> Path:
    """Dynamically determine registry path from environment or default."""
    env_path = os.environ.get("SARI_REGISTRY_FILE")
    if env_path:
        return Path(env_path).resolve()
    # Default path may be blocked in sandboxed/test environments.
    if _ensure_writable_dir(REGISTRY_FILE):
        return REGISTRY_FILE
    # Fallback to a temp location when home directory is not writable.
    _ensure_writable_dir(_FALLBACK_REGISTRY)
    return _FALLBACK_REGISTRY.resolve()

class ServerRegistry:
    """
    Manages the 'server.json' registry for Sari Daemons using robust file locking.
    Registry v2 (SSOT):
      - daemons: boot_id -> {host, port, pid, start_ts, last_seen_ts, draining, version}
      - workspaces: workspace_root -> {boot_id, last_active_ts, http_port, http_host}
    """

    VERSION = "2.0"

    def __init__(self):
        # Delay lock initialization so tests can patch get_registry_path first.
        self._lock = None

    def _ensure_lock(self) -> FileLock:
        reg_file = get_registry_path()
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        if self._lock is None:
            # Use FileLock for cross-platform reliability and built-in timeouts
            self._lock = FileLock(str(reg_file.with_suffix(".json.lock")), timeout=10)
        if not reg_file.exists():
            # Initialize registry file under lock without re-entering _ensure_lock.
            with self._lock:
                if not reg_file.exists():
                    self._atomic_write(self._empty())
        return self._lock

    def _empty(self) -> Dict[str, Any]:
        return {"version": self.VERSION, "daemons": {}, "workspaces": {}}

    def _normalize_workspace_root(self, workspace_root: str) -> str:
        from sari.core.workspace import WorkspaceManager
        return WorkspaceManager.normalize_path(workspace_root)

    def _safe_load(self, content: str) -> Dict[str, Any]:
        try:
            data = json.loads(content)
        except Exception:
            data = {}
        return self._ensure_v2(data)

    def _load_unlocked(self) -> Dict[str, Any]:
        reg_file = get_registry_path()
        try:
            with open(reg_file, "r") as f:
                return self._safe_load(f.read())
        except FileNotFoundError:
            return self._empty()

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        reg_file = get_registry_path()
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = reg_file.parent / f"{reg_file.name}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, reg_file)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise

    def _ensure_v2(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self._empty()
        version = data.get("version")
        if version == self.VERSION and "daemons" in data and "workspaces" in data:
            return data

        # Migrate legacy schema (v1) if needed
        if "instances" in data and isinstance(data["instances"], dict):
            now = time.time()
            migrated = self._empty()
            for ws, info in data["instances"].items():
                if not isinstance(info, dict): continue
                ws_norm = ws # Simplification for migration
                pid = info.get("pid")
                port = info.get("port")
                if pid is None or port is None: continue
                
                boot_id = f"legacy-{pid}-{port}"
                migrated["daemons"][boot_id] = {
                    "host": "127.0.0.1", "port": int(port), "pid": int(pid),
                    "start_ts": info.get("start_ts") or now, "last_seen_ts": now,
                    "draining": False, "version": info.get("version") or "legacy",
                }
                migrated["workspaces"][ws_norm] = {"boot_id": boot_id, "last_active_ts": now}
            return migrated

        return self._empty()

    def _load(self) -> Dict[str, Any]:
        lock = self._ensure_lock()
        with lock:
            return self._load_unlocked()

    def _save(self, data: Dict[str, Any]):
        lock = self._ensure_lock()
        with lock:
            self._atomic_write(data)

    def _update(self, updater) -> None:
        lock = self._ensure_lock()
        with lock:
            data = self._load_unlocked()
            updater(data)
            self._atomic_write(data)

    def _is_process_alive(self, pid: Optional[int]) -> bool:
        if not pid: return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, PermissionError):
            return False

    def _prune_dead_locked(self, data: Dict[str, Any]) -> None:
        daemons = data.get("daemons", {})
        workspaces = data.get("workspaces", {})
        dead = [bid for bid, info in daemons.items() if not self._is_process_alive(info.get("pid"))]
        for bid in dead: daemons.pop(bid, None)
        if dead:
            workspaces = {ws: info for ws, info in workspaces.items() if info.get("boot_id") not in dead}
        data["daemons"] = daemons
        data["workspaces"] = workspaces

    def register_daemon(self, boot_id: str, host: str, port: int, pid: int, version: str = "") -> None:
        def _upd(data):
            self._prune_dead_locked(data)
            daemons = data.setdefault("daemons", {})
            daemons[boot_id] = {
                "host": host, "port": port, "pid": pid,
                "start_ts": daemons.get(boot_id, {}).get("start_ts") or time.time(),
                "last_seen_ts": time.time(),
                "draining": bool(daemons.get(boot_id, {}).get("draining", False)),
                "version": version or daemons.get(boot_id, {}).get("version", ""),
            }
        self._update(_upd)

    def get_daemon(self, boot_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        daemon = (data.get("daemons") or {}).get(boot_id)
        if not daemon: return None
        if not self._is_process_alive(daemon.get("pid")):
            self.unregister_daemon(boot_id)
            return None
        return daemon

    def get_workspace(self, workspace_root: str) -> Optional[Dict[str, Any]]:
        ws = self._normalize_workspace_root(workspace_root)
        data = self._load()
        return (data.get("workspaces") or {}).get(ws)

    def list_workspaces_for_boot(self, boot_id: str) -> Iterable[str]:
        data = self._load()
        workspaces = data.get("workspaces", {})
        return [ws for ws, info in workspaces.items() if info.get("boot_id") == boot_id]

    def resolve_workspace_daemon(self, workspace_root: str) -> Optional[Dict[str, Any]]:
        ws = self._normalize_workspace_root(workspace_root)
        data = self._load()
        workspaces = data.get("workspaces", {})
        info = workspaces.get(ws)
        if not info: return None
        boot_id = info.get("boot_id")
        daemon = (data.get("daemons") or {}).get(boot_id)
        if not daemon:
            self.unregister_workspace(ws)
            return None
        if not self._is_process_alive(daemon.get("pid")):
            self.unregister_daemon(boot_id)
            return None
        merged = dict(daemon)
        merged["boot_id"] = boot_id
        return merged

    def unregister_daemon(self, boot_id: str) -> None:
        def _upd(data):
            daemons = data.get("daemons", {})
            daemons.pop(boot_id, None)
            workspaces = data.get("workspaces", {})
            data["workspaces"] = {ws: info for ws, info in workspaces.items() if info.get("boot_id") != boot_id}
        self._update(_upd)

    def set_daemon_draining(self, boot_id: str, draining: bool = True) -> None:
        def _upd(data):
            daemons = data.get("daemons", {})
            if boot_id in daemons:
                daemons[boot_id]["draining"] = bool(draining)
                daemons[boot_id]["last_seen_ts"] = time.time()
        self._update(_upd)

    def resolve_daemon_by_endpoint(self, host: str, port: int) -> Optional[Dict[str, Any]]:
        data = self._load()
        daemons = data.get("daemons", {})
        for bid, info in daemons.items():
            if str(info.get("host")) == str(host) and int(info.get("port")) == int(port):
                if self._is_process_alive(info.get("pid")):
                    res = dict(info); res["boot_id"] = bid
                    return res
        return None

    def resolve_latest_daemon(self, workspace_root: Optional[str] = None, allow_draining: bool = True) -> Optional[Dict[str, Any]]:
        data = self._load()
        daemons = data.get("daemons", {}) or {}
        workspaces = data.get("workspaces", {}) or {}

        if workspace_root:
            ws = self._normalize_workspace_root(workspace_root)
            info = workspaces.get(ws)
            if info:
                boot_id = info.get("boot_id")
                daemon = daemons.get(boot_id)
                if daemon and (allow_draining or not daemon.get("draining")) and self._is_process_alive(daemon.get("pid")):
                    res = dict(daemon)
                    res["boot_id"] = boot_id
                    return res

        best = None
        best_ts = -1.0
        for bid, info in daemons.items():
            if not allow_draining and info.get("draining"):
                continue
            if not self._is_process_alive(info.get("pid")):
                continue
            ts = float(info.get("last_seen_ts") or 0.0)
            if ts > best_ts:
                best_ts = ts
                best = (bid, info)

        if best:
            res = dict(best[1])
            res["boot_id"] = best[0]
            return res
        return None

    def find_free_port(self, host: str = "127.0.0.1", start_port: int = 47790, max_tries: int = 200) -> int:
        import socket
        port = int(start_port)
        for _ in range(max_tries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, port))
                    return port
                except OSError:
                    port += 1
        # Fallback: ask OS for an ephemeral port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            return s.getsockname()[1]

    def _is_nested_pair(self, a: str, b: str) -> bool:
        if not a or not b or a == b: return False
        return a.startswith(b + os.sep) or b.startswith(a + os.sep)

    def _dedupe_nested_workspaces_locked(self, data: Dict[str, Any], preferred_ws: Optional[str] = None) -> None:
        workspaces = dict(data.get("workspaces", {}))
        if not workspaces: return

        if preferred_ws and preferred_ws in workspaces:
            remove = [ws for ws in workspaces.keys() if ws != preferred_ws and self._is_nested_pair(ws, preferred_ws)]
            for ws in remove: workspaces.pop(ws, None)
            data["workspaces"] = workspaces
            return

        # Global dedupe fallback
        ordered = sorted(workspaces.items(), key=lambda kv: float(kv[1].get("last_active_ts", 0.0)), reverse=True)
        kept = {}
        for ws, info in ordered:
            if any(self._is_nested_pair(ws, k) for k in kept.keys()): continue
            kept[ws] = info
        data["workspaces"] = kept

    def set_workspace_http(self, workspace_root: str, http_port: int, http_host: Optional[str] = None, http_pid: Optional[int] = None) -> None:
        def _upd(data):
            workspaces = data.setdefault("workspaces", {})
            ws = self._normalize_workspace_root(workspace_root)
            payload = dict(workspaces.get(ws, {}))
            payload["http_port"] = int(http_port)
            if http_host: payload["http_host"] = str(http_host)
            if http_pid: payload["http_pid"] = int(http_pid)
            payload["last_active_ts"] = time.time()
            workspaces[ws] = payload
            self._dedupe_nested_workspaces_locked(data, preferred_ws=ws)
        self._update(_upd)

    def set_workspace(self, workspace_root: str, boot_id: str) -> None:
        def _upd(data):
            workspaces = data.setdefault("workspaces", {})
            ws = self._normalize_workspace_root(workspace_root)
            payload = dict(workspaces.get(ws, {}))
            payload["boot_id"] = boot_id
            payload["last_active_ts"] = time.time()
            workspaces[ws] = payload
            self._dedupe_nested_workspaces_locked(data, preferred_ws=ws)
        self._update(_upd)

    def unregister_workspace(self, workspace_root: str) -> None:
        def _upd(data):
            ws = self._normalize_workspace_root(workspace_root)
            data.get("workspaces", {}).pop(ws, None)
        self._update(_upd)

    def touch_daemon(self, boot_id: str) -> None:
        """Update last_seen_ts for a daemon to indicate it is still alive."""
        def _upd(data):
            daemons = data.get("daemons", {})
            if boot_id in daemons:
                daemons[boot_id]["last_seen_ts"] = time.time()
        self._update(_upd)

    def prune_dead(self) -> None:
        """Public method to prune dead daemons and associated workspaces."""
        self._update(self._prune_dead_locked)
