import asyncio
import inspect
import os
import sys
import signal
import logging
import ipaddress
import threading
import time
import uuid
from pathlib import Path

# Ensure project root is in sys.path
SCRIPT_DIR = Path(__file__).parent
# Go up 3 levels: sari/mcp/daemon.py -> mcp/ -> sari/ -> (repo root)
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sari.mcp.session import Session
from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.settings import settings
from sari.core.constants import DEFAULT_DAEMON_HOST, DEFAULT_DAEMON_PORT
from sari.mcp.trace import trace

def _resolve_log_dir() -> Path:
    val = settings.LOG_DIR
    if val:
        return Path(os.path.expanduser(val)).resolve()
    return WorkspaceManager.get_global_log_dir()

def _init_logging() -> None:
    log_dir = _resolve_log_dir()
    handlers = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(log_dir / "daemon.log"))
    except Exception:
        # Fall back to /tmp if the default log dir is not writable.
        try:
            tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "sari"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            handlers.insert(0, logging.FileHandler(tmp_dir / "daemon.log"))
        except Exception:
            pass

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
    except Exception:
        pass
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
        self.boot_id = env_boot_id or uuid.uuid4().hex
        if not env_boot_id:
            os.environ["SARI_BOOT_ID"] = self.boot_id
            
        self._stop_event = threading.Event()
        self._heartbeat_thread = None
        self._idle_since = None
        self._drain_since = None
        self.httpd = None
        self.http_host = None
        self.http_port = None
        trace("daemon_init", host=self.host, port=self.port)

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
            shared = Registry.get_instance().get_or_create(workspace_root, persistent=True)
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
            self._registry.set_daemon_http(self.boot_id, actual_port, http_host=host, http_pid=os.getpid())
            logger.info(f"Started daemon HTTP gateway on {host}:{actual_port}")
        except Exception as e:
            logger.error(f"Failed to start daemon HTTP gateway: {e}")

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        interval = settings.DAEMON_HEARTBEAT_SEC
        idle_sec = settings.DAEMON_IDLE_SEC
        idle_with_active = settings.DAEMON_IDLE_WITH_ACTIVE
        drain_grace = settings.DAEMON_DRAIN_GRACE_SEC

        from sari.mcp.workspace_registry import Registry
        workspace_registry = Registry.get_instance()

        while not self._stop_event.is_set():
            try:
                self._registry.touch_daemon(self.boot_id)
                daemon_info = self._registry.get_daemon(self.boot_id) or {}
                draining = bool(daemon_info.get("draining"))
                active_count = workspace_registry.active_count()
                now = time.time()

                if draining:
                    if self._drain_since is None:
                        self._drain_since = now
                    if active_count == 0:
                        self.shutdown()
                        break
                    if drain_grace > 0 and now - self._drain_since >= drain_grace:
                        self.shutdown()
                        break
                else:
                    self._drain_since = None
                    if idle_sec > 0:
                        if active_count == 0 or idle_with_active:
                            last_activity = workspace_registry.get_last_activity_ts()
                            if last_activity <= 0:
                                last_activity = now
                            if self._idle_since is None:
                                self._idle_since = last_activity
                            # Keep a small grace window to avoid flapping right after startup.
                            if now - last_activity >= (idle_sec + 1.0):
                                self.shutdown()
                                break
                        else:
                            self._idle_since = None
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")

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

        session = Session(reader, writer)
        await session.handle_connection()

        logger.info(f"Closed connection from {addr}")
        trace("daemon_client_closed", addr=str(addr))

    def stop(self):
        """Public stop method for external control (tests, etc.)."""
        self.shutdown()

    def shutdown(self):
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        logger.info("Initiating graceful shutdown...")
        trace("daemon_shutdown_start")

        # 1. Stop Server Loop
        if self.server:
            try:
                self.server.close()
                if self._loop and self._loop.is_running():
                    # Ensure close callback is processed
                    pass
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

        # 3. Kill any remaining children (Safety Net)
        try:
            import multiprocessing
            for child in multiprocessing.active_children():
                logger.warning(f"Terminating lingering child process: {child.pid}")
                child.terminate()
        except Exception:
            pass

        self._unregister_daemon()
        self._cleanup_legacy_pid_file()
        logger.info("Shutdown sequence complete.")
        trace("daemon_shutdown_complete")

async def main():
    daemon = SariDaemon()

    # Handle signals
    loop = asyncio.get_running_loop()
    stop = asyncio.Future()

    def _handle_signal():
        stop.set_result(None)

    loop.add_signal_handler(signal.SIGTERM, _handle_signal)
    loop.add_signal_handler(signal.SIGINT, _handle_signal)

    daemon_task = asyncio.create_task(daemon.start_async())

    logger.info("Daemon started. Press Ctrl+C to stop.")

    try:
        await stop
    finally:
        logger.info("Stopping daemon...")
        daemon.shutdown()
        # Wait for server to close? asyncio.start_server manages this in async with
        # but we created a task.
        # Actually server.serve_forever() runs until cancelled.
        daemon_task.cancel()
        try:
            await daemon_task
        except asyncio.CancelledError:
            pass
        logger.info("Daemon stopped.")

if __name__ == "__main__":
    asyncio.run(main())
