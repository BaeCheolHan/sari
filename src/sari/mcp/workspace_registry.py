import threading
import logging
import time
import os
from typing import Dict, Optional, Any
from sari.core.config.manager import ConfigManager
from sari.core.db.main import LocalSearchDB
from sari.core.indexer import Indexer
from sari.core.search_engine import SearchEngine
from sari.core.workspace import WorkspaceManager

logger = logging.getLogger("sari.registry")

class SharedState:
    def __init__(self, workspace_root: str):
        self.workspace_root = WorkspaceManager.normalize_path(workspace_root)
        try:
            from sari.core.settings import settings
            WorkspaceManager.set_settings(settings)
        except Exception:
            pass
        self.root_id = WorkspaceManager.root_id_for_workspace(workspace_root)
        
        # 1. Load Layered Config (Phase 1)
        self.config_manager = ConfigManager(workspace_root)
        self.config_data = self.config_manager.resolve_final_config()
        
        # 2. Init DB (Phase 4) - Force global DB only
        db_path = str(WorkspaceManager.get_global_db_path())
        local_dir = WorkspaceManager.get_workspace_data_dir(workspace_root)
        if local_dir.exists():
            logger.info(f"Workspace-local DB detected; ignoring in favor of global DB: {db_path}")
        else:
            logger.info(f"Using global DB: {db_path}")
            
        self.db = LocalSearchDB(db_path)
        try:
            from sari.core.settings import settings
            self.db.set_settings(settings)
        except Exception:
            pass
        
        # 3. Init Indexer (Phase 3 & 2)
        from sari.core.config import Config
        defaults = Config.get_defaults(workspace_root)
        defaults["workspace_roots"] = [workspace_root]
        defaults["include_ext"] = self.config_data.get("final_extensions", defaults["include_ext"])
        defaults["include_files"] = self.config_data.get("final_filenames", defaults["include_files"])
        defaults["exclude_dirs"] = self.config_data.get("final_exclude_dirs", defaults["exclude_dirs"])
        defaults["exclude_globs"] = self.config_data.get("final_exclude_globs", defaults["exclude_globs"])
        defaults["gitignore_lines"] = self.config_data.get("gitignore_lines", defaults.get("gitignore_lines", []))
        cfg_obj = Config(**defaults)
        
        self.indexer = Indexer(cfg_obj, self.db, logger=logger)
        
        # 4. Init File Watcher (Real-time sync)
        try:
            from sari.core.watcher import FileWatcher
            def on_change(evt):
                if self.indexer:
                    # evt.path is already the file path
                    self.indexer.index_file(evt.path)
            
            self.watcher = FileWatcher([str(self.workspace_root)], on_change_callback=on_change, logger=logger)
        except Exception as e:
            logger.warning(f"Failed to initialize FileWatcher: {e}")
            self.watcher = None
        
        # 5. Wire Scheduling Coordinator for Read-Priority (Deprecated/Unused)
        # self.db.coordinator = self.indexer.coordinator
        
        try:
            from sari.core.engine_registry import get_default_engine
            self.db.set_engine(get_default_engine(self.db, cfg_obj, cfg_obj.workspace_roots))
        except Exception:
            self.search_engine = SearchEngine(self.db)
            self.db.set_engine(self.search_engine)
        
        # 5. MCP Server instance (Lazy import to avoid circularity)
        from sari.mcp.server import LocalSearchMCPServer
        self.server = LocalSearchMCPServer(workspace_root)
        
        self.http_port = None
        self.http_host = None
        self.httpd = None
        self.last_activity = time.time()
        self.ref_count = 0
        self.persistent = False
        self._lock = threading.Lock()

    def start(self): 
        # 0. Ensure Root Exists
        try:
            self.db.ensure_root(self.root_id, str(self.workspace_root))
        except Exception as e:
            logger.error(f"Failed to ensure root for {self.workspace_root}: {e}")

        # 1. Start Indexer
        threading.Thread(target=self.indexer.run_forever, daemon=True).start()
        
        # 1.1 Start Watcher
        if hasattr(self, "watcher") and self.watcher:
            try:
                self.watcher.start()
                logger.info(f"FileWatcher started for {self.workspace_root}")
            except Exception as e:
                logger.error(f"Failed to start FileWatcher: {e}")
    
    def touch(self):
        with self._lock:
            self.last_activity = time.time()
        
    def stop(self):
        try:
            if hasattr(self, "watcher") and self.watcher:
                self.watcher.stop()
            self.indexer.stop()
        except Exception as e:
            logger.error(f"Error stopping indexer/watcher: {e}")
        
        # 1. Cleanup Registry
        try:
            from sari.core.server_registry import ServerRegistry
            ServerRegistry().unregister_workspace(self.workspace_root)
        except Exception:
            pass

        # 2. Close DB
        try:
            self.db.close_all()
        except Exception:
            try: self.db.close()
            except: pass

class Registry:
    _instance = None
    _lock = threading.Lock()
    def __init__(self): self._sessions: Dict[str, SharedState] = {}
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None: cls._instance = cls()
        return cls._instance
    def get_or_create(self, workspace_root: str, persistent: bool = False, track_ref: bool = True) -> SharedState:
        with self._lock:
            if workspace_root not in self._sessions:
                session = SharedState(workspace_root)
                session.persistent = bool(persistent)
                session.start()
                self._sessions[workspace_root] = session
            elif persistent:
                self._sessions[workspace_root].persistent = True
            if track_ref:
                self._sessions[workspace_root].ref_count += 1
            elif self._sessions[workspace_root].ref_count <= 0:
                self._sessions[workspace_root].ref_count = 1
            self._sessions[workspace_root].touch()
            return self._sessions[workspace_root]
            
    def touch_workspace(self, workspace_root: str):
        with self._lock:
            if workspace_root in self._sessions:
                self._sessions[workspace_root].touch()
                
    def release(self, workspace_root: str):
        with self._lock:
            if workspace_root in self._sessions:
                state = self._sessions[workspace_root]
                state.ref_count = max(0, state.ref_count - 1)
                state.touch()
                if state.ref_count == 0 and not state.persistent:
                    state.stop()
                    del self._sessions[workspace_root]

    def shutdown_all(self):
        with self._lock:
            for s in self._sessions.values(): s.stop()
            self._sessions.clear()
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.ref_count > 0)
    def has_persistent(self) -> bool:
        with self._lock:
            return any(s.persistent for s in self._sessions.values())
    def get_last_activity_ts(self) -> float: 
        with self._lock:
            return max((s.last_activity for s in self._sessions.values() if s.ref_count > 0), default=0.0)
