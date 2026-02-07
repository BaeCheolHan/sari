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

from sari.mcp.session import Session
from sari.core.workspace import WorkspaceManager
from sari.core.server_registry import ServerRegistry
from sari.core.settings import settings

logger = logging.getLogger("mcp-daemon")

class SariDaemon:
    def __init__(self, host: str = None, port: int = None):
        self.host = host or settings.DAEMON_HOST
        self.port = int(port or settings.DAEMON_PORT)
        self.server = None
        self._loop = None
        self._pinned_workspace_root = None
        self._registry = ServerRegistry()
        
        # Priority 1: Registry is the only source of truth. No legacy PID files.
        env_boot_id = (os.environ.get("SARI_BOOT_ID") or "").strip()
        self.boot_id = env_boot_id or uuid.uuid4().hex
        if not env_boot_id:
            os.environ["SARI_BOOT_ID"] = self.boot_id
            
        self._stop_event = threading.Event()
        self._heartbeat_thread = None
        self._idle_since = None
        self._drain_since = None

    def _register_daemon(self):
        try:
            sari_version = settings.VERSION
            host = (self.host or "127.0.0.1").strip()
            port = int(self.port)
            pid = os.getpid()
            # Priority 6: Fail-fast policy
            self._registry.register_daemon(self.boot_id, host, port, pid, version=sari_version)
            # Double check registration
            info = self._registry.get_daemon(self.boot_id)
            if not info:
                logger.critical("FATAL: Failed to verify daemon registration.")
                sys.exit(1)
            logger.info(f"Registered daemon {self.boot_id} on {host}:{port}")
        except Exception as e:
            logger.critical(f"FATAL: Daemon registration error: {e}")
            sys.exit(1)

    def _unregister_daemon(self):
        try:
            self._registry.unregister_daemon(self.boot_id)
            logger.info(f"Unregistered daemon {self.boot_id}")
        except Exception as e:
            logger.error(f"Failed to unregister daemon: {e}")

    def _autostart_workspace(self) -> None:
        # Priority 3: Always autostart primary workspace if possible
        workspace_root = settings.WORKSPACE_ROOT or WorkspaceManager.resolve_workspace_root()
        try:
            from sari.mcp.workspace_registry import Registry
            # This will now trigger HTTP server boot inside Registry
            Registry.get_instance().get_or_create(workspace_root, persistent=True)
            self._pinned_workspace_root = workspace_root
            logger.info(f"Auto-started workspace session for {workspace_root}")
        except Exception as e:
            logger.error(f"Failed to auto-start workspace: {e}")

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        interval = settings.DAEMON_HEARTBEAT_SEC
        from sari.mcp.workspace_registry import Registry
        workspace_registry = Registry.get_instance()

        while not self._stop_event.is_set():
            try:
                self._registry.touch_daemon(self.boot_id)
                daemon_info = self._registry.get_daemon(self.boot_id) or {}
                if daemon_info.get("draining"):
                    # Priority 9: Handle drain state
                    if workspace_registry.active_count() == 0:
                        logger.info("Drain complete. Shutting down daemon.")
                        self.shutdown()
                        break
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
            self._stop_event.wait(interval)

    async def start_async(self):
        host = (self.host or "127.0.0.1").strip()
        
        # Priority 1: Prevent duplicate starts if already in registry and alive
        existing = self._registry.resolve_daemon_by_endpoint(host, self.port)
        if existing:
            # Check if it's really alive
            if os.getpid() != existing.get("pid"):
                raise SystemExit(f"sari daemon already running on {host}:{self.port} (PID: {existing['pid']})")

        # Priority 5: Auto-port allocation if default is busy but not in registry
        try:
            self.server = await asyncio.start_server(self.handle_client, host, self.port)
        except OSError:
            logger.warning(f"Default port {self.port} busy. Using auto-port.")
            self.server = await asyncio.start_server(self.handle_client, host, 0)
            self.port = self.server.sockets[0].getsockname()[1]

        os.environ["SARI_DAEMON_HOST"] = host
        os.environ["SARI_DAEMON_PORT"] = str(self.port)
        self._loop = asyncio.get_running_loop()
        
        self._register_daemon()
        self._autostart_workspace()
        self._start_heartbeat()

        addr = self.server.sockets[0].getsockname()
        logger.info(f"Sari Daemon serving on {addr}")

        async with self.server:
            await self.server.serve_forever()

    def start(self):
        try:
            asyncio.run(self.start_async())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        session = Session(reader, writer)
        await session.handle_connection()

    def shutdown(self):
        if self._stop_event.is_set(): return
        self._stop_event.set()
        logger.info("Initiating graceful shutdown...")
        from sari.mcp.workspace_registry import Registry
        Registry.get_instance().shutdown_all()
        self._unregister_daemon()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    SariDaemon().start()