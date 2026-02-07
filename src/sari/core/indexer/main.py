import concurrent.futures
import threading
import time
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from sari.core.config import Config
from sari.core.db import LocalSearchDB
from sari.core.indexer.scanner import Scanner

@dataclass
class IndexStatus:
    index_ready: bool = False
    indexed_files: int = 0
    symbols_extracted: int = 0
    scan_started_ts: int = 0
    scan_finished_ts: int = 0
    scanned_files: int = 0
    errors: int = 0

def _ultra_turbo_worker(path):
    """
    Stateless Worker: ONLY performs IO and Parsing.
    Now reports errors back to parent for debugging functional integrity.
    """
    try:
        from pathlib import Path
        import sys
        import traceback
        
        p = Path(path)
        content = p.read_bytes()
        ext = p.suffix.lower()
        
        symbols = []
        error_msg = ""
        try:
            from sari.core.parsers.factory import ParserFactory
            parser = ParserFactory.get_parser(ext)
            if parser:
                text = content.decode("utf-8", errors="ignore")
                symbols, _ = parser.extract(str(p), text)
                if not symbols:
                    error_msg = "Parser returned empty symbols"
            else:
                error_msg = f"No parser found for extension {ext}"
        except Exception as e:
            error_msg = f"Parser error: {str(e)}"
        
        # Mapping Truth: (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, symbol_id)
        return (str(p), content, len(content), [list(s) for s in symbols], error_msg)
    except Exception as e:
        return (path, b"", 0, [], f"Fatal worker error: {str(e)}")

class Indexer:
    def __init__(self, cfg: Config, db: LocalSearchDB, logger=None):
        self.cfg, self.db, self.logger = cfg, db, logger
        self.status = IndexStatus()
        self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=os.cpu_count() or 4)
        self.scanner = Scanner(cfg)
        self.max_workers = os.cpu_count() or 4
        self._active_roots_lock = threading.Lock() # Priority 1: Proper lock for thread safety
        self._stop_event = threading.Event()

    def scan_once(self):
        scan_started_ts = int(time.time())
        self.status.scan_started_ts = scan_started_ts
        self.status.indexed_files = 0
        self.status.symbols_extracted = 0
        
        futures = []
        for root_path in self.cfg.workspace_roots:
            root = Path(root_path).expanduser().resolve()
            if not root.exists(): continue
            
            from sari.core.workspace import WorkspaceManager
            root_id = WorkspaceManager.root_id_for_workspace(str(root))
            self.db.ensure_root(root_id, str(root))

            # Priority 3: Preload Metadata Snapshot
            metadata_snapshot = self.db.get_all_file_metadata(root_id)
            seen_paths = []

            for p, st, excluded in self.scanner.iter_file_entries(root):
                if excluded: continue
                path_str = str(p)
                seen_paths.append(path_str)

                # Priority 3: Fast Meta Check (Memory lookup)
                prev = metadata_snapshot.get(path_str)
                if prev and int(st.st_mtime) == int(prev[0]) and int(st.st_size) == int(prev[1]):
                    continue # Skip unchanged

                # Throttle check
                if hasattr(self.db, "coordinator") and self.db.coordinator:
                    while self.db.coordinator.should_throttle_indexing():
                        time.sleep(0.5)

                futures.append(self._executor.submit(_ultra_turbo_worker, path_str))
                if len(futures) % 500 == 0: 
                    self._process_completed(futures, root_id=root_id)
            
            self._process_completed(futures, wait_all=True, root_id=root_id)
            
            # Priority 4: Batch update last_seen for all found files
            self.db.update_last_seen_batch(seen_paths, scan_started_ts)

            # Priority 1: Barrier synchronization before pruning
            # We wait for the DBWriter to commit all the upserts/updates
            sync_event = threading.Event()
            from .db_writer import DbTask
            # Assuming SharedState wired a DBWriter to self.db.writer or similar
            # If not using a persistent writer thread, we can call finalize directly
            self.db.finalize_turbo_batch()

            # Priority 1: Safe Pruning (Unseen deletion)
            unseen = self.db.get_unseen_paths(root_id, scan_started_ts)
            if unseen:
                self.db.prune_stale_data(root_id, unseen)
                if self.logger: self.logger.info(f"Pruned {len(unseen)} stale files")

        self.status.index_ready = True
        self.status.scan_finished_ts = int(time.time())

    def _process_completed(self, futures, wait_all=False, root_id="root"):
        if not futures: return
        done, not_done = concurrent.futures.wait(
            futures, 
            timeout=0 if not wait_all else None,
            return_when=concurrent.futures.FIRST_COMPLETED if not wait_all else concurrent.futures.ALL_COMPLETED
        )
        
        file_rows = []
        all_symbol_rows = []
        
        for f in done:
            try:
                res = f.result()
                if res:
                    path, content, size, symbols, error = res
                    if error:
                        self.status.errors += 1
                        if self.logger:
                            self.logger.debug(f"Worker report for {path}: {error}")
                    
                    # Correct Mapping for File model (20 columns):
                    # path, rel_path, root_id, repo, mtime, size, content, content_hash, 
                    # fts_content, last_seen_ts, deleted_ts, parse_status, parse_reason, 
                    # ast_status, ast_reason, is_binary, is_minified, sampled, content_bytes, metadata_json
                    file_rows.append((
                        path, path, root_id, "repo", 0, size, content, "", 
                        "", 0, 0, "ok", "", "ok", "", 0, 0, 0, size, "{}"
                    ))
                    self.status.indexed_files += 1
                    
                    for s in symbols:
                        all_symbol_rows.append(tuple(s))
                        self.status.symbols_extracted += 1
            except Exception as e:
                self.status.errors += 1
                if self.logger:
                    self.logger.error(f"Error getting future result: {e}")
            futures.remove(f)
            
        if file_rows: 
            self.db.upsert_files_turbo(file_rows)
            # CRITICAL: Flush files to main DB before symbols are added
            self.db.finalize_turbo_batch()

        if all_symbol_rows: 
            self.db.upsert_symbols_tx(None, all_symbol_rows, root_id=root_id)

    def index_file(self, path: str):
        """Re-index a single file immediately. (Used by Watcher)"""
        if not os.path.exists(path):
            # If file is deleted, mark as deleted in DB
            self.db.mark_deleted(path)
            if self.logger: self.logger.debug(f"File deleted: {path}")
            return

        try:
            from sari.core.workspace import WorkspaceManager
            root_id = WorkspaceManager.root_id_for_workspace_root_containing(path, self.cfg.workspace_roots)
            
            # Submit to worker
            res = _ultra_turbo_worker(path)
            if res:
                path, content, size, symbols, error = res
                # Update DB
                self.db.upsert_files_turbo([(path, path, root_id, "repo", 0, size, content, "", "", 0, 0, "ok", "", "ok", "", 0, 0, 0, size, "{}")])
                self.db.finalize_turbo_batch()
                if symbols:
                    self.db.upsert_symbols_tx(None, [tuple(s) for s in symbols], root_id=root_id)
                if self.logger: self.logger.info(f"Incrementally indexed: {path}")
        except Exception as e:
            if self.logger: self.logger.error(f"Error during incremental indexing of {path}: {e}")

    def run_forever(self):
        """
        Pure Event-Driven Mode (Phase 11): 
        Optimized for 15+ repositories.
        1. Perform one full scan on startup to bridge any offline changes.
        2. Enter idle state and rely on 'FileWatcher' (watchdog) for real-time updates.
        """
        if self.logger: self.logger.info("Indexer starting initial reconciliation scan...")
        try:
            self.scan_once()
            if self.logger: self.logger.info("Reconciliation complete. Indexer now in Pure Event-Driven mode (Idle).")
        except Exception as e:
            if self.logger: self.logger.error(f"Initial scan failed: {e}")

        # Stay alive without polling. 
        # Future enhancement: periodic health check every 24h or via manual trigger.
        while True:
            time.sleep(3600)

    def stop(self):
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def request_rescan(self):
        """Non-blocking rescan request."""
        # In a more complex setup, this would set a flag or trigger an event
        pass
