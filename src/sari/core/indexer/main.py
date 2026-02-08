import os
import time
import logging
import concurrent.futures
import threading
from typing import List, Optional, Dict, Any
from pathlib import Path
from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from .worker import IndexWorker
from sari.core.workspace import WorkspaceManager

class Indexer:
    def __init__(self, config: Config, db: LocalSearchDB, logger=None, **kwargs):
        self.config = config
        self.db = db
        self.logger = logger or logging.getLogger("sari.indexer")
        self.status = IndexStatus()
        self.worker = IndexWorker(config, db, self.logger, None)
        # 병렬 처리를 위한 ThreadPoolExecutor (I/O 바운드 작업에 적합)
        max_workers = kwargs.get("max_workers", os.cpu_count() or 4)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._rescan_event = threading.Event()
        self._scan_lock = threading.Lock()

    def scan_once(self):
        self.status.scan_started_ts = int(time.time())
        all_files = []
        for root in self.config.workspace_roots:
            rid = WorkspaceManager.root_id(root)
            self.db.ensure_root(rid, str(root))
            for path in Path(root).rglob("*"):
                if path.is_file() and self.config.should_index(str(path)):
                    all_files.append((root, path, rid))
        
        self.status.scanned_files = len(all_files)
        
        # 병렬 처리
        futures = []
        for root, path, rid in all_files:
            try:
                st = path.stat()
                future = self._executor.submit(
                    self.worker.process_file_task,
                    root, path, st, int(time.time()), st.st_mtime, True, root_id=rid
                )
                futures.append(future)
            except Exception:
                self.status.errors += 1
        
        # 결과 수집 및 DB 저장
        file_rows = []
        all_symbols = []
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res and res.get("type") in ("changed", "new"):
                    self.status.indexed_files += 1
                    
                    # 워커 결과를 DB 튜플 형식으로 변환
                    # File 모델 필드 순서 (20개):
                    # path, rel_path, root_id, repo, mtime, size, content, content_hash, fts_content,
                    # last_seen, deleted_ts, parse_status, parse_reason, ast_status, ast_reason,
                    # is_binary, is_minified, sampled, content_bytes, metadata_json
                    file_row = (
                        res.get("path", ""),  # path (Absolute path to match Symbol FK)
                        res.get("rel", ""),  # rel_path
                        res.get("root_id", "root"),  # root_id
                        res.get("repo", ""),  # repo
                        res.get("mtime", 0),  # mtime
                        res.get("size", 0),  # size
                        res.get("content", ""),  # content
                        res.get("content_hash", ""),  # content_hash
                        res.get("fts_content", ""),  # fts_content
                        res.get("scan_ts", 0),  # last_seen
                        0,  # deleted_ts (0 = 활성 파일)
                        res.get("parse_status", "ok"),  # parse_status
                        res.get("parse_reason", ""),  # parse_reason
                        res.get("ast_status", "skipped"),  # ast_status
                        res.get("ast_reason", ""),  # ast_reason
                        res.get("is_binary", 0),  # is_binary
                        res.get("is_minified", 0),  # is_minified
                        0,  # sampled
                        res.get("content_bytes", 0),  # content_bytes
                        res.get("metadata_json", "{}")  # metadata_json
                    )
                    file_rows.append(file_row)
                    
                    # 심볼 수집
                    symbols = res.get("symbols", [])
                    if symbols:
                        # Augment symbols with root_id to ensure upsert_symbols_tx uses correct root
                        # Symbol format from worker: (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, sid)
                        # We want to prepend the root_id so upsert_symbols_tx can use it.
                        root_id = res.get("root_id", "root")
                        augmented_symbols = []
                        for s in symbols:
                            # Construct Format A (12-element):
                            # (sid, path, root_id, name, kind, line, end_line, content, parent_name, metadata, docstring, qualname)
                            if len(s) >= 11:
                                augmented = (
                                    s[10], # sid
                                    s[0],  # path
                                    root_id, # root_id
                                    s[1],  # name
                                    s[2],  # kind
                                    s[3],  # line
                                    s[4],  # end_line
                                    s[5],  # content
                                    s[6],  # parent
                                    s[7],  # metadata
                                    s[8],  # docstring
                                    s[9]   # qualname
                                )
                                augmented_symbols.append(augmented)
                        
                        all_symbols.extend(augmented_symbols)
                        self.status.symbols_extracted += len(symbols)
            except Exception as e:
                self.status.errors += 1
                if self.logger:
                    self.logger.error(f"Error processing future result: {e}")
        
        # DB에 저장
        if file_rows:
            self.db.upsert_files_turbo(file_rows)
        
        self.db.finalize_turbo_batch()
        
        # 심볼 저장
        if all_symbols:
            # symbols는 리스트의 리스트 형식: [[path, name, kind, line, end_line, ...]]
            try:
                # Use collected symbols which now are in Format A with explicit root_id
                self.db.upsert_symbols_tx(None, all_symbols)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error storing symbols: {e}")
        
        self.status.scan_finished_ts = int(time.time())
        self.status.index_ready = True

    def _run_scan_once(self):
        with self._scan_lock:
            self.scan_once()

    def request_rescan(self):
        self._rescan_event.set()

    def index_file(self, _path: str):
        self.request_rescan()

    def run_forever(self):
        next_due = time.time()
        while True:
            if self._rescan_event.is_set() or time.time() >= next_due:
                self._rescan_event.clear()
                self._run_scan_once()
                next_due = time.time() + self.config.scan_interval_seconds
            time.sleep(0.2)

    def stop(self):
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
    

class IndexStatus:
    def __init__(self):
        self.index_ready = False
        self.indexed_files = 0
        self.symbols_extracted = 0
        self.scan_started_ts = 0
        self.scan_finished_ts = 0
        self.scanned_files = 0
        self.errors = 0