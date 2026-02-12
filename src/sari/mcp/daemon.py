# ruff: noqa: E402
import asyncio
import inspect
import os
import signal
import logging
import ipaddress
import threading
import time
import json
import queue
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from sari.mcp.session import Session
from sari.core.policy_engine import load_daemon_policy
from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.settings import settings
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.core.utils.uuid7 import uuid7_hex
from sari.mcp.trace import trace
from sari.mcp.stabilization.warning_sink import warning_sink, warn

LOG_INIT_FAILED = "LOG_INIT_FAILED"
PID_FILE_RESOLVE_FAILED = "PID_FILE_RESOLVE_FAILED"
REAP_STALE_REFS_FAILED = "REAP_STALE_REFS_FAILED"
CHILD_TERMINATE_FAILED = "CHILD_TERMINATE_FAILED"
SIGNAL_HANDLER_REG_FAILED = "SIGNAL_HANDLER_REG_FAILED"
ANALYTICS_FLUSH_FAILED = "ANALYTICS_FLUSH_FAILED"
LEASE_REAP_FAILED = "LEASE_REAP_FAILED"

EVENT_LEASE_ISSUE = "LEASE_ISSUE"
EVENT_LEASE_RENEW = "LEASE_RENEW"
EVENT_LEASE_REVOKE = "LEASE_REVOKE"
EVENT_CONN_CLOSED = "CONN_CLOSED"
EVENT_HEARTBEAT_TICK = "HEARTBEAT_TICK"
EVENT_SHUTDOWN_REQUEST = "SHUTDOWN_REQUEST"
DEFAULT_EVENT_DRAIN_MAX = 256


@dataclass(slots=True)
class DaemonEvent:
    event_type: str
    lease_id: str = ""
    conn_id: str = ""
    ts: float = field(default_factory=time.time)
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeStateSnapshot:
    draining: bool
    active_count: int
    socket_active: int
    lease_active: int
    workers_inflight: int
    indexing_active: bool
    last_activity: float


class RuntimeStateProvider:
    """Collects runtime signals used by daemon controller policy logic."""

    def __init__(self, daemon: "SariDaemon", workspace_registry: object):
        self._daemon = daemon
        self._workspace_registry = workspace_registry

    def collect(self) -> RuntimeStateSnapshot:
        daemon_info = self._daemon._registry.get_daemon(self._daemon.boot_id) or {}
        active_count = int(self._workspace_registry.active_count() or 0)
        socket_active = int(self._daemon._get_active_connections() or 0)
        lease_active = int(self._daemon.active_lease_count() or 0)
        workers_inflight = int(self._daemon._workers_inflight() or 0)
        indexing_active = bool(
            getattr(self._workspace_registry, "has_indexing_activity", lambda: False)()
        )
        last_activity = float(self._workspace_registry.get_last_activity_ts() or 0.0)
        return RuntimeStateSnapshot(
            draining=bool(daemon_info.get("draining")),
            active_count=active_count,
            socket_active=socket_active,
            lease_active=lease_active,
            workers_inflight=workers_inflight,
            indexing_active=indexing_active,
            last_activity=last_activity,
        )


def _resolve_log_dir() -> Path:
    val = settings.LOG_DIR
    if val:
        return Path(os.path.expanduser(val)).resolve()
    return WorkspaceManager.get_global_log_dir()


def _is_managed_log_file(name: str) -> bool:
    lowered = str(name or "").lower()
    return (
        lowered.endswith(".log")
        or ".log." in lowered
        or lowered.endswith(".log.swp")
    )


def _cleanup_old_logs(
        log_dir: Path,
        retention_days: int,
        now_ts: float | None = None) -> int:
    keep_days = int(retention_days or 0)
    if keep_days <= 0:
        return 0
    now = float(now_ts if now_ts is not None else time.time())
    cutoff = now - (keep_days * 86400)
    removed = 0
    for entry in log_dir.iterdir():
        try:
            if not entry.is_file():
                continue
            if not _is_managed_log_file(entry.name):
                continue
            if float(entry.stat().st_mtime) >= cutoff:
                continue
            entry.unlink(missing_ok=True)
            removed += 1
        except Exception:
            continue
    return removed


def _init_logging() -> None:
    log_dir = _resolve_log_dir()
    handlers = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        removed = _cleanup_old_logs(log_dir, getattr(settings, "LOG_RETENTION_DAYS", 14))
        if removed:
            logging.getLogger("mcp-daemon.bootstrap").info(
                "Removed %s stale log files under %s",
                removed,
                str(log_dir),
            )
        handlers.insert(0, logging.FileHandler(log_dir / "daemon.log"))
    except Exception as e:
        logging.getLogger("mcp-daemon.bootstrap").warning(
            "Failed to initialize daemon log file in primary directory",
            exc_info=True,
        )
        warn(
            LOG_INIT_FAILED,
            "_init_logging.primary",
            exc=e,
            extra={"stage": "primary", "log_dir": str(log_dir)},
        )
        # Fall back to /tmp if the default log dir is not writable.
        try:
            tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "sari"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            handlers.insert(0, logging.FileHandler(tmp_dir / "daemon.log"))
        except Exception as fallback_e:
            logging.getLogger("mcp-daemon.bootstrap").warning(
                "Failed to initialize daemon log file in fallback directory",
                exc_info=True,
            )
            warn(
                LOG_INIT_FAILED,
                "_init_logging.fallback",
                exc=fallback_e,
                extra={"stage": "fallback", "log_dir": str(tmp_dir)},
            )

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
    )

_init_logging()
logger = logging.getLogger("mcp-daemon")

DEFAULT_HOST = DEFAULT_DAEMON_HOST
DEFAULT_PORT = DEFAULT_DAEMON_PORT
PID_FILE = WorkspaceManager.get_global_data_dir() / "daemon.pid"

def _pid_file() -> Path:
    try:
        if isinstance(PID_FILE, Path):
            return PID_FILE
    except Exception as e:
        logging.getLogger("mcp-daemon").warning(
            "Failed to validate PID file path; using fallback",
            exc_info=True,
        )
        warn(
            PID_FILE_RESOLVE_FAILED,
            "_pid_file.validate",
            exc=e,
            extra={"pid_file_repr": repr(PID_FILE)},
        )
    if not isinstance(PID_FILE, Path):
        logging.getLogger("mcp-daemon").warning(
            "PID file path is invalid type; using fallback path",
        )
        warn(
            PID_FILE_RESOLVE_FAILED,
            "_pid_file.type",
            extra={
                "pid_file_type": type(PID_FILE).__name__,
                "pid_file_repr": repr(PID_FILE),
            },
        )
    return WorkspaceManager.get_global_data_dir() / "daemon.pid"

class SariDaemon:
    def __init__(self, host: str = None, port: int = None):
        self.host = host or settings.DAEMON_HOST
        self.port = int(port or settings.DAEMON_PORT)
        self.server = None
        self._loop = None
        self._pinned_workspace_root = None
        self._registry = ServerRegistry()
        
        # Use existing boot ID if provided, otherwise generate new
        env_boot_id = (os.environ.get("SARI_BOOT_ID") or "").strip()
        self.boot_id = env_boot_id or uuid7_hex()
        if not env_boot_id:
            os.environ["SARI_BOOT_ID"] = self.boot_id
            
        self._stop_event = threading.Event()
        self._heartbeat_thread = None
        self._controller_thread = None
        self._idle_since = None
        self._drain_since = None
        self.httpd = None
        self.http_host = None
        self.http_port = None
        self._active_connections = 0
        self._conn_lock = threading.Lock()
        self._lease_lock = threading.Lock()
        self._events: queue.SimpleQueue[DaemonEvent] = queue.SimpleQueue()
        self._events_lock = threading.Lock()
        self._controller_wakeup = threading.Event()
        self._event_queue_depth = 0
        self._last_event_ts = 0.0
        self._active_leases: dict[str, dict[str, object]] = {}
        self._recent_leases: deque[dict[str, object]] = deque(maxlen=50)
        self._last_reap_at = 0.0
        self._reaper_last_run_at = 0.0
        self._reap_fail_count = 0
        self._signals_disabled = False
        self._shutdown_intent = False
        self._last_shutdown_reason = ""
        self._shutdown_once = threading.Event()
        self._suicide_state = "idle"  # idle | grace | stopping
        self._no_client_since = 0.0
        self._autostop_no_client_since = None  # backward-compat tests
        self._grace_deadline = 0.0
        self._shutdown_inhibit_since: float | None = None
        trace("daemon_init", host=self.host, port=self.port)

    @property
    def last_shutdown_reason(self) -> str:
        return str(self._last_shutdown_reason or "")

    def active_lease_count(self) -> int:
        with self._lease_lock:
            return len(self._active_leases)

    def leases_snapshot(self) -> list[dict[str, object]]:
        with self._lease_lock:
            active = [dict(v) for v in self._active_leases.values()]
            recent = [dict(v) for v in self._recent_leases]
            return active + recent

    def _lease_ttl_sec(self) -> float:
        return float(load_daemon_policy(settings_obj=settings).lease_ttl_sec)

    def _issue_lease(self, client_hint: str, ttl_sec: float | None = None) -> str:
        lease_id = uuid7_hex()
        now = time.time()
        ttl = float(ttl_sec if ttl_sec is not None else self._lease_ttl_sec())
        with self._lease_lock:
            self._active_leases[lease_id] = {
                "id": lease_id,
                "client_hint": str(client_hint or ""),
                "last_seen": now,
                "expires_at": now + ttl,
                "revoked_reason": "",
                "state": "active",
            }
        self._emit_runtime_markers()
        return lease_id

    def _renew_lease(self, lease_id: str, ttl_sec: float | None = None) -> bool:
        ttl = float(ttl_sec if ttl_sec is not None else self._lease_ttl_sec())
        now = time.time()
        with self._lease_lock:
            item = self._active_leases.get(str(lease_id or ""))
            if not item:
                return False
            item["last_seen"] = now
            item["expires_at"] = now + ttl
            item["state"] = "active"
            item["revoked_reason"] = ""
        self._emit_runtime_markers()
        return True

    def _revoke_lease(self, lease_id: str, reason: str = "", now_ts: float | None = None) -> None:
        now = float(now_ts if now_ts is not None else time.time())
        with self._lease_lock:
            removed = self._active_leases.pop(str(lease_id or ""), None)
            if isinstance(removed, dict):
                removed["state"] = "revoked"
                removed["revoked_reason"] = str(reason or "revoked")
                removed["revoked_at"] = now
                self._recent_leases.appendleft(dict(removed))
        self._emit_runtime_markers()

    def _reap_expired_leases(self, now_ts: float | None = None) -> int:
        now = float(now_ts if now_ts is not None else time.time())
        with self._lease_lock:
            expired = [
                str(lease_id)
                for lease_id, info in self._active_leases.items()
                if float(info.get("expires_at", 0) or 0) <= now
            ]
        for lease_id in expired:
            self._revoke_lease(lease_id, reason="ttl_expired", now_ts=now)
        reaped = len(expired)
        if reaped:
            self._last_reap_at = now
        self._reaper_last_run_at = now
        self._emit_runtime_markers()
        return reaped

    def _enqueue_event(self, event: DaemonEvent) -> None:
        self._events.put(event)
        with self._events_lock:
            self._event_queue_depth += 1
            self._last_event_ts = float(event.ts or time.time())
        self._controller_wakeup.set()

    def _enqueue_lease_event(
        self,
        event_type: str,
        *,
        lease_id: str,
        client_hint: str = "",
        reason: str = "",
    ) -> None:
        self._enqueue_event(
            DaemonEvent(
                event_type=str(event_type or ""),
                lease_id=str(lease_id or ""),
                payload={"client_hint": str(client_hint or ""), "reason": str(reason or "")},
            )
        )

    def _enqueue_shutdown_request(self, reason: str) -> None:
        self._enqueue_event(
            DaemonEvent(
                event_type=EVENT_SHUTDOWN_REQUEST,
                payload={"reason": str(reason or "manual")},
            )
        )

    def _drain_events(self, max_events: int) -> list[DaemonEvent]:
        limit = max(1, int(max_events or DEFAULT_EVENT_DRAIN_MAX))
        raw: list[DaemonEvent] = []
        for _ in range(limit):
            try:
                ev = self._events.get_nowait()
                with self._events_lock:
                    self._event_queue_depth = max(0, self._event_queue_depth - 1)
                raw.append(ev)
            except queue.Empty:
                break
        if not raw:
            return []
        # Tick events are coalesced to one and processed first so timers progress
        # even under heavy control-event bursts.
        ticks = [ev for ev in raw if str(getattr(ev, "event_type", "")) == EVENT_HEARTBEAT_TICK]
        non_ticks = [ev for ev in raw if str(getattr(ev, "event_type", "")) != EVENT_HEARTBEAT_TICK]
        if ticks:
            return [ticks[-1], *non_ticks]
        return non_ticks

    def _apply_lease_events(self, now_ts: float | None = None, max_events: int = DEFAULT_EVENT_DRAIN_MAX) -> int:
        now = float(now_ts if now_ts is not None else time.time())
        applied = 0
        ttl = self._lease_ttl_sec()
        for ev in self._drain_events(max_events=max_events):
            event_type = str(getattr(ev, "event_type", "") or "")
            lease_id = str(getattr(ev, "lease_id", "") or "")
            payload = dict(getattr(ev, "payload", {}) or {})
            client_hint = str(payload.get("client_hint", "") or "")
            reason = str(payload.get("reason", "") or "")
            applied += 1
            if event_type == EVENT_HEARTBEAT_TICK:
                continue
            if not lease_id:
                if event_type == EVENT_SHUTDOWN_REQUEST:
                    try:
                        self.shutdown(reason=reason or "manual")
                    except TypeError:
                        self.shutdown()
                continue
            if event_type == EVENT_LEASE_ISSUE:
                with self._lease_lock:
                    self._active_leases[lease_id] = {
                        "id": lease_id,
                        "client_hint": client_hint,
                        "last_seen": now,
                        "expires_at": now + ttl,
                        "revoked_reason": "",
                        "state": "active",
                    }
            elif event_type == EVENT_LEASE_RENEW:
                self._renew_lease(lease_id, ttl_sec=ttl)
            elif event_type in {EVENT_LEASE_REVOKE, EVENT_CONN_CLOSED}:
                self._revoke_lease(lease_id, reason=reason or "revoked", now_ts=now)
        if applied:
            self._emit_runtime_markers()
        return applied

    def _workers_alive(self) -> list[int]:
        try:
            import multiprocessing
            out: list[int] = []
            for child in multiprocessing.active_children():
                pid = int(getattr(child, "pid", -1) or -1)
                if pid > 0:
                    out.append(pid)
            return out
        except Exception:
            return []

    def _workers_inflight(self) -> int:
        return len(self._workers_alive())

    def _set_suicide_state(self, state: str) -> None:
        normalized = str(state or "idle")
        if normalized not in {"idle", "grace", "stopping"}:
            normalized = "idle"
        if self._suicide_state != normalized:
            self._suicide_state = normalized
            self._emit_runtime_markers()

    def _emit_runtime_markers(self) -> None:
        now = time.time()
        grace_remaining = max(0.0, float(self._grace_deadline or 0.0) - now) if self._suicide_state == "grace" else 0.0
        os.environ["SARI_DAEMON_ACTIVE_LEASES_COUNT"] = str(self.active_lease_count())
        os.environ["SARI_DAEMON_LAST_REAP_AT"] = str(float(self._last_reap_at or 0.0))
        os.environ["SARI_DAEMON_REAPER_LAST_RUN_AT"] = str(float(self._reaper_last_run_at or 0.0))
        os.environ["SARI_DAEMON_SHUTDOWN_INTENT"] = "1" if self._shutdown_intent else ""
        os.environ["SARI_DAEMON_LAST_SHUTDOWN_REASON"] = str(self._last_shutdown_reason or "")
        os.environ["SARI_DAEMON_SHUTDOWN_REASON"] = str(self._last_shutdown_reason or "")
        os.environ["SARI_DAEMON_SUICIDE_STATE"] = str(self._suicide_state or "idle")
        os.environ["SARI_DAEMON_NO_CLIENT_SINCE"] = str(float(self._no_client_since or 0.0))
        os.environ["SARI_DAEMON_GRACE_REMAINING"] = str(float(grace_remaining))
        os.environ["SARI_DAEMON_GRACE_REMAINING_MS"] = str(int(max(0.0, float(grace_remaining)) * 1000.0))
        os.environ["SARI_DAEMON_SHUTDOWN_ONCE_SET"] = "1" if self._shutdown_once.is_set() else ""
        with self._events_lock:
            os.environ["SARI_DAEMON_LAST_EVENT_TS"] = str(float(self._last_event_ts or 0.0))
            os.environ["SARI_DAEMON_EVENT_QUEUE_DEPTH"] = str(int(self._event_queue_depth or 0))
        try:
            os.environ["SARI_DAEMON_LEASES"] = json.dumps(self.leases_snapshot(), ensure_ascii=True)
        except Exception:
            os.environ["SARI_DAEMON_LEASES"] = "[]"
        try:
            os.environ["SARI_DAEMON_WORKERS_ALIVE"] = json.dumps(self._workers_alive(), ensure_ascii=True)
        except Exception:
            os.environ["SARI_DAEMON_WORKERS_ALIVE"] = "[]"

    def _request_shutdown(self, reason: str) -> None:
        self._set_suicide_state("stopping")
        self._enqueue_shutdown_request(reason)

    def mark_signals_disabled(self) -> None:
        self._signals_disabled = True
        os.environ["SARI_DAEMON_SIGNALS_DISABLED"] = "1"
        warn(
            SIGNAL_HANDLER_REG_FAILED,
            "SariDaemon.mark_signals_disabled",
            extra={"pid": os.getpid()},
        )

    def _cleanup_legacy_pid_file(self):
        """Best-effort cleanup for legacy pid file; registry is the SSOT."""
        try:
            pid_file = _pid_file()
            if pid_file.exists():
                pid_file.unlink()
                logger.info("Removed legacy daemon.pid file")
        except Exception as e:
            logger.debug(f"Failed to remove legacy daemon.pid file: {e}")

    def _register_daemon(self):
        try:
            sari_version = settings.VERSION
            host = (self.host or "127.0.0.1").strip()
            port = int(self.port)
            pid = os.getpid()
            self._registry.register_daemon(self.boot_id, host, port, pid, version=sari_version)
            logger.info(f"Registered daemon {self.boot_id} on {host}:{port}")
            trace("daemon_registered", boot_id=self.boot_id, host=host, port=port, pid=pid, version=sari_version)
        except Exception as e:
            logger.error(f"Failed to register daemon: {e}")
            trace("daemon_register_error", error=str(e))

    def _unregister_daemon(self):
        try:
            self._registry.unregister_daemon(self.boot_id)
            logger.info(f"Unregistered daemon {self.boot_id}")
            trace("daemon_unregistered", boot_id=self.boot_id)
        except Exception as e:
            logger.error(f"Failed to unregister daemon: {e}")
            trace("daemon_unregister_error", error=str(e))

    def _autostart_workspace(self) -> None:
        if not settings.DAEMON_AUTOSTART:
            return

        workspace_root = settings.WORKSPACE_ROOT or WorkspaceManager.resolve_workspace_root()

        try:
            from sari.mcp.workspace_registry import Registry
            # Do not pin a permanent reference here; allow autostop when CLI sessions close.
            shared = Registry.get_instance().get_or_create(
                workspace_root,
                persistent=False,
                track_ref=False,
            )
            self._pinned_workspace_root = workspace_root
            self._start_http_gateway(shared)
            logger.info(f"Auto-started workspace session for {workspace_root}")
        except Exception as e:
            logger.error(f"Failed to auto-start workspace session: {e}")
        try:
            # Record workspace mapping even if warm-up failed.
            self._registry.set_workspace(
                workspace_root,
                self.boot_id,
                http_port=self.http_port,
                http_host=self.http_host,
            )
        except Exception as e:
            logger.error(f"Failed to record workspace mapping: {e}")

    def _start_http_gateway(self, shared_state) -> None:
        if self.httpd is not None:
            return
        try:
            from sari.core.http_server import serve_forever

            host = "127.0.0.1"
            port = int(settings.HTTP_API_PORT)
            httpd, actual_port = serve_forever(
                host,
                port,
                shared_state.db,
                shared_state.indexer,
                version=settings.VERSION,
                workspace_root=str(shared_state.workspace_root),
                mcp_server=shared_state.server,
                shared_http_gateway=True,
            )
            self.httpd = httpd
            self.http_host = host
            self.http_port = actual_port
            # Shared gateway runs in-process with daemon; avoid a misleading separate http_pid.
            self._registry.set_daemon_http(self.boot_id, actual_port, http_host=host)
            logger.info(f"Started daemon HTTP gateway on {host}:{actual_port}")
        except Exception as e:
            logger.error(f"Failed to start daemon HTTP gateway: {e}")

    def _inc_active_connections(self) -> None:
        with self._conn_lock:
            self._active_connections += 1

    def _dec_active_connections(self) -> None:
        with self._conn_lock:
            self._active_connections = max(0, self._active_connections - 1)

    def _get_active_connections(self) -> int:
        with self._conn_lock:
            return self._active_connections

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _start_controller(self) -> None:
        if self._controller_thread and self._controller_thread.is_alive():
            return
        self._controller_thread = threading.Thread(target=self._controller_loop, daemon=True)
        self._controller_thread.start()

    def _heartbeat_loop(self) -> None:
        daemon_policy = load_daemon_policy(settings_obj=settings)
        interval = daemon_policy.heartbeat_sec
        while not self._stop_event.is_set():
            self._enqueue_event(DaemonEvent(event_type=EVENT_HEARTBEAT_TICK))
            self._controller_wakeup.wait(interval)
            self._controller_wakeup.clear()

    def _controller_loop(self) -> None:
        daemon_policy = load_daemon_policy(settings_obj=settings)
        interval = daemon_policy.heartbeat_sec
        idle_sec = daemon_policy.idle_sec
        idle_with_active = daemon_policy.idle_with_active
        drain_grace = daemon_policy.drain_grace_sec
        autostop_grace = daemon_policy.autostop_grace_sec
        autostop_enabled = daemon_policy.autostop_enabled
        inhibit_max = daemon_policy.shutdown_inhibit_max_sec
        from sari.mcp.workspace_registry import Registry
        workspace_registry = Registry.get_instance()
        runtime_provider = RuntimeStateProvider(self, workspace_registry)

        while not self._stop_event.is_set():
            try:
                now = time.time()
                self._apply_lease_events(now_ts=now)
                self._reap_expired_leases(now_ts=now)
                self._registry.touch_daemon(self.boot_id)
                runtime = runtime_provider.collect()
                draining = bool(runtime.draining)
                active_count = int(runtime.active_count)
                socket_active = int(runtime.socket_active)
                lease_active = int(runtime.lease_active)
                workers_inflight = int(runtime.workers_inflight)
                reap_fn = getattr(workspace_registry, "reap_stale_refs", None)
                if callable(reap_fn):
                    try:
                        reap_fn(max(1, float(autostop_grace)))
                    except Exception as reap_error:
                        self._reap_fail_count += 1
                        logger.warning("Failed to reap stale workspace refs", exc_info=True)
                        warn(
                            REAP_STALE_REFS_FAILED,
                            "SariDaemon._controller_loop.reap_stale_refs",
                            exc=reap_error,
                            extra={"fail_count": self._reap_fail_count},
                        )

                if socket_active == 0 and active_count > 0 and callable(reap_fn):
                    try:
                        reap_fn(max(1, float(autostop_grace)))
                        active_count = workspace_registry.active_count()
                    except Exception as reap_stale_error:
                        self._reap_fail_count += 1
                        logger.warning("Failed to reap stale refs while no socket clients", exc_info=True)
                        warn(
                            LEASE_REAP_FAILED,
                            "SariDaemon._controller_loop.reap_stale_refs.no_socket",
                            exc=reap_stale_error,
                            extra={"fail_count": self._reap_fail_count},
                        )

                active_count = max(active_count, socket_active, lease_active)

                if self._suicide_state == "stopping":
                    pass
                elif draining:
                    if self._drain_since is None:
                        self._drain_since = now
                    if active_count == 0:
                        self._request_shutdown("draining_no_clients")
                    elif drain_grace > 0 and now - self._drain_since >= drain_grace:
                        self._request_shutdown("draining_grace_exceeded")
                else:
                    self._drain_since = None
                    if active_count > 0:
                        self._set_suicide_state("idle")
                        self._no_client_since = 0.0
                        self._autostop_no_client_since = None
                        self._grace_deadline = 0.0
                        self._shutdown_inhibit_since = None
                    elif autostop_enabled:
                        if self._suicide_state == "idle":
                            self._set_suicide_state("grace")
                            self._no_client_since = now
                            self._autostop_no_client_since = now
                            self._grace_deadline = now + float(autostop_grace)
                        elif self._autostop_no_client_since and self._no_client_since <= 0:
                            self._no_client_since = float(self._autostop_no_client_since)
                            self._grace_deadline = self._no_client_since + float(autostop_grace)
                        if self._suicide_state == "grace" and now >= float(self._grace_deadline or 0.0):
                            if workers_inflight <= 0:
                                self._request_shutdown("autostop_no_clients")
                            elif self._shutdown_inhibit_since is None:
                                self._shutdown_inhibit_since = now
                            elif now - self._shutdown_inhibit_since >= inhibit_max:
                                self._request_shutdown("autostop_inhibit_timeout")

                    if idle_sec > 0:
                        indexing_active = bool(runtime.indexing_active)
                        if (active_count == 0 or idle_with_active) and not indexing_active:
                            last_activity = float(runtime.last_activity)
                            if last_activity <= 0:
                                last_activity = now
                            if self._idle_since is None:
                                self._idle_since = last_activity
                            if now - last_activity >= (idle_sec + 1.0):
                                self._request_shutdown("idle_timeout")
                        else:
                            self._idle_since = None
                self._emit_runtime_markers()
            except Exception as e:
                logger.error(f"Controller loop failed: {e}")

            self._stop_event.wait(interval)

    async def start_async(self):
        host = (self.host or "127.0.0.1").strip()
        try:
            is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host.lower() == "localhost"

        if not is_loopback:
            raise SystemExit(
                f"sari daemon refused to start: host must be loopback only (127.0.0.1/localhost/::1). got={host}. "
                "Remote access is NOT supported for security."
            )

        # Prevent duplicate starts on the same endpoint
        existing = self._registry.resolve_daemon_by_endpoint(host, self.port)
        if existing:
            raise SystemExit(f"sari daemon already running on {host}:{self.port} (PID: {existing['pid']})")

        self._cleanup_legacy_pid_file()
        trace("daemon_start_async", host=host, port=self.port)
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        os.environ["SARI_DAEMON_HOST"] = host
        os.environ["SARI_DAEMON_PORT"] = str(self.port)
        self._loop = asyncio.get_running_loop()
        self._register_daemon()
        self._autostart_workspace()
        self._start_controller()
        self._start_heartbeat()

        addr = self.server.sockets[0].getsockname()
        logger.info(f"Sari Daemon serving on {addr}")
        trace("daemon_listening", addr=str(addr))

        async with self.server:
            runner = self.server.serve_forever()
            if inspect.isawaitable(runner):
                await runner
            else:
                # Test doubles may provide non-awaitable mocks.
                while not self._stop_event.is_set():
                    await asyncio.sleep(0.1)

    def start(self):
        try:
            asyncio.get_running_loop()
            return self.start_async()
        except RuntimeError:
            try:
                asyncio.run(self.start_async())
            except asyncio.CancelledError:
                pass
            return None

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        logger.info(f"Accepted connection from {addr}")
        trace("daemon_client_accepted", addr=str(addr))
        self._inc_active_connections()
        lease_id = uuid7_hex()
        self._enqueue_lease_event(EVENT_LEASE_ISSUE, lease_id=lease_id, client_hint=str(addr))
        try:
            session = Session(
                reader,
                writer,
                on_activity=lambda: self._enqueue_lease_event(EVENT_LEASE_RENEW, lease_id=lease_id),
                on_connection_closed=lambda: self._enqueue_lease_event(EVENT_LEASE_REVOKE, lease_id=lease_id, reason="connection_closed"),
            )
            await session.handle_connection()
        finally:
            self._enqueue_lease_event(EVENT_LEASE_REVOKE, lease_id=lease_id, reason="handle_client_finally")
            self._dec_active_connections()
            logger.info(f"Closed connection from {addr}")
            trace("daemon_client_closed", addr=str(addr))

    def stop(self):
        """Public stop method for external control (tests, etc.)."""
        self._enqueue_shutdown_request("stop")

    def shutdown(self, reason: str = "manual"):
        if self._shutdown_once.is_set():
            return
        self._shutdown_once.set()
        self._stop_event.set()
        self._shutdown_intent = True
        self._last_shutdown_reason = str(reason or "manual")
        self._set_suicide_state("stopping")
        self._emit_runtime_markers()

        logger.info("Initiating graceful shutdown... reason=%s", self._last_shutdown_reason)
        trace("daemon_shutdown_start", reason=self._last_shutdown_reason)

        # 1. Stop Server Loop
        if self.server:
            try:
                self.server.close()
                wait_closed = getattr(self.server, "wait_closed", None)
                if callable(wait_closed):
                    close_result = wait_closed()
                    if inspect.isawaitable(close_result):
                        try:
                            if self._loop and self._loop.is_running():
                                fut = asyncio.run_coroutine_threadsafe(close_result, self._loop)
                                fut.result(timeout=5.0)
                            else:
                                asyncio.run(close_result)
                        except Exception:
                            logger.warning("Failed waiting for daemon server close", exc_info=True)
            except Exception as e:
                logger.debug(f"Error during server close: {e}")

        # 2. Shutdown all workspaces (Stops Indexers and ProcessPools)
        from sari.mcp.workspace_registry import Registry
        try:
            logger.info("Shutting down workspace registry and indexers...")
            Registry.get_instance().shutdown_all()
        except Exception as e:
            logger.error(f"Error shutting down registry: {e}")

        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception as e:
                logger.debug(f"Error shutting down HTTP gateway: {e}")

        try:
            from sari.mcp.stabilization.analytics_queue import drain_analytics
            drain_analytics(limit=max(1, int(settings.get_int("SARI_ANALYTICS_DRAIN_LIMIT", 2048))))
        except Exception as analytics_error:
            logger.warning("Failed to flush analytics queue during shutdown", exc_info=True)
            warn(
                ANALYTICS_FLUSH_FAILED,
                "SariDaemon.shutdown.analytics_flush",
                exc=analytics_error,
            )

        # 3. Kill any remaining children (Safety Net)
        try:
            import multiprocessing
            failed_child_pids: list[int] = []
            for child in multiprocessing.active_children():
                logger.warning(f"Terminating lingering child process: {child.pid}")
                try:
                    child.terminate()
                    join = getattr(child, "join", None)
                    if callable(join):
                        join(timeout=3.0)
                    is_alive = getattr(child, "is_alive", None)
                    still_alive = bool(is_alive() if callable(is_alive) else False)
                    if still_alive:
                        kill = getattr(child, "kill", None)
                        if callable(kill):
                            kill()
                            if callable(join):
                                join(timeout=1.0)
                except Exception as child_error:
                    failed_child_pids.append(int(getattr(child, "pid", -1) or -1))
                    logger.warning("Failed to terminate lingering child process", exc_info=True)
                    warn(
                        CHILD_TERMINATE_FAILED,
                        "SariDaemon.shutdown.child_terminate",
                        exc=child_error,
                        extra={"pid": int(getattr(child, "pid", -1) or -1)},
                    )
            if failed_child_pids:
                warn(
                    CHILD_TERMINATE_FAILED,
                    "SariDaemon.shutdown.child_terminate_summary",
                    extra={"pids": [p for p in failed_child_pids if p > 0]},
                )
        except Exception as e:
            logger.warning("Failed while terminating lingering child processes", exc_info=True)
            warn(
                CHILD_TERMINATE_FAILED,
                "SariDaemon.shutdown.terminate_loop",
                exc=e,
            )

        self._unregister_daemon()
        self._cleanup_legacy_pid_file()
        self._emit_runtime_markers()
        logger.info("Shutdown sequence complete.")
        trace("daemon_shutdown_complete", reason=self._last_shutdown_reason)

async def main():
    daemon = SariDaemon()

    # Handle signals
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    stop_reason = {"value": "signal"}

    def _handle_signal(sig_name: str):
        stop_reason["value"] = f"signal:{sig_name}"
        stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: _handle_signal("SIGTERM"))
        loop.add_signal_handler(signal.SIGINT, lambda: _handle_signal("SIGINT"))
    except Exception as e:
        # Some environments (tests/platforms) may not support signal handlers.
        logger.warning("Signal handler registration failed; daemon will rely on other shutdown paths", exc_info=True)
        warn(
            SIGNAL_HANDLER_REG_FAILED,
            "main.add_signal_handler",
            exc=e,
        )
        if hasattr(daemon, "mark_signals_disabled"):
            daemon.mark_signals_disabled()

    daemon_task = asyncio.create_task(daemon.start_async())
    stop_task = asyncio.create_task(stop_event.wait())

    logger.info("Daemon started. Press Ctrl+C to stop.")

    try:
        done, _ = await asyncio.wait(
            {daemon_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if daemon_task in done:
            # Propagate startup/runtime errors immediately.
            exc = daemon_task.exception()
            if exc is not None:
                raise exc
    finally:
        stop_task.cancel()
        logger.info("Stopping daemon...")
        if hasattr(daemon, "_enqueue_shutdown_request"):
            daemon._enqueue_shutdown_request(stop_reason["value"])
            for _ in range(30):
                if bool(getattr(daemon, "_shutdown_once", None) and daemon._shutdown_once.is_set()):
                    break
                await asyncio.sleep(0.1)
        else:
            daemon.shutdown()
        if not daemon_task.done():
            daemon_task.cancel()
        try:
            await daemon_task
        except asyncio.CancelledError:
            pass
        logger.info("Daemon stopped.")

if __name__ == "__main__":
    asyncio.run(main())
