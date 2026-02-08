import os
import time
import json
import logging
import threading
import multiprocessing
import tempfile
import concurrent.futures
from typing import List, Optional, Dict, Any
from pathlib import Path
from sari.core.config.main import Config
from sari.core.db.main import LocalSearchDB
from .worker import IndexWorker
from sari.core.workspace import WorkspaceManager

def _scan_to_db(config: Config, db: LocalSearchDB, logger: logging.Logger) -> Dict[str, Any]:
    status = {
        "scan_started_ts": int(time.time()),
        "scan_finished_ts": 0,
        "scanned_files": 0,
        "indexed_files": 0,
        "symbols_extracted": 0,
        "errors": 0,
        "index_version": "",
    }
    worker = IndexWorker(config, db, logger, None)
    max_workers = os.cpu_count() or 4
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        all_files = []
        for root in config.workspace_roots:
            rid = WorkspaceManager.root_id(root)
            db.ensure_root(rid, str(root))
            for path in Path(root).rglob("*"):
                if path.is_file() and config.should_index(str(path)):
                    all_files.append((root, path, rid))

        status["scanned_files"] = len(all_files)
        futures = []
        now = int(time.time())
        for root, path, rid in all_files:
            try:
                st = path.stat()
                futures.append(executor.submit(
                    worker.process_file_task,
                    root, path, st, now, st.st_mtime, True, root_id=rid
                ))
            except Exception:
                status["errors"] += 1

        file_rows = []
        all_symbols = []
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                if res and res.get("type") in ("changed", "new"):
                    status["indexed_files"] += 1
                    file_row = (
                        res.get("path", ""),
                        res.get("rel", ""),
                        res.get("root_id", "root"),
                        res.get("repo", ""),
                        res.get("mtime", 0),
                        res.get("size", 0),
                        res.get("content", ""),
                        res.get("content_hash", ""),
                        res.get("fts_content", ""),
                        res.get("scan_ts", 0),
                        0,
                        res.get("parse_status", "ok"),
                        res.get("parse_reason", ""),
                        res.get("ast_status", "skipped"),
                        res.get("ast_reason", ""),
                        res.get("is_binary", 0),
                        res.get("is_minified", 0),
                        0,
                        res.get("content_bytes", 0),
                        res.get("metadata_json", "{}"),
                    )
                    file_rows.append(file_row)

                    symbols = res.get("symbols", [])
                    if symbols:
                        root_id = res.get("root_id", "root")
                        augmented_symbols = []
                        for s in symbols:
                            if len(s) >= 11:
                                augmented_symbols.append((
                                    s[10],
                                    s[0],
                                    root_id,
                                    s[1],
                                    s[2],
                                    s[3],
                                    s[4],
                                    s[5],
                                    s[6],
                                    s[7],
                                    s[8],
                                    s[9],
                                ))
                        all_symbols.extend(augmented_symbols)
                        status["symbols_extracted"] += len(symbols)
            except Exception as e:
                status["errors"] += 1
                if logger:
                    logger.error(f"Error processing future result: {e}")

        if file_rows:
            db.upsert_files_turbo(file_rows)
        db.finalize_turbo_batch()
        if all_symbols:
            try:
                db.upsert_symbols_tx(None, all_symbols)
            except Exception as e:
                if logger:
                    logger.error(f"Error storing symbols: {e}")
        status["scan_finished_ts"] = int(time.time())
        status["index_version"] = str(status["scan_finished_ts"])
        return status
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

def _worker_build_snapshot(config_dict: Dict[str, Any], snapshot_path: str, status_path: str, log_path: str) -> None:
    logger = logging.getLogger("sari.indexer.worker")
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
            logger.setLevel(logging.INFO)
        except Exception:
            pass
    try:
        cfg = Config(**config_dict)
        db = LocalSearchDB(snapshot_path, logger=logger, journal_mode="delete")
        status = _scan_to_db(cfg, db, logger)
        db.close_all()
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump({"ok": True, "status": status, "snapshot_path": snapshot_path}, f)
    except Exception as e:
        try:
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": str(e), "snapshot_path": snapshot_path}, f)
        except Exception:
            pass

class Indexer:
    def __init__(self, config: Config, db: LocalSearchDB, logger=None, **kwargs):
        self.config = config
        self.db = db
        self.logger = logger or logging.getLogger("sari.indexer")
        self.status = IndexStatus()
        self.worker = IndexWorker(config, db, self.logger, None)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.indexing_enabled = True
        self.indexer_mode = "worker"
        self._rescan_event = threading.Event()
        self._stop_event = threading.Event()
        self._scan_lock = threading.Lock()
        self._worker_proc: Optional[multiprocessing.Process] = None
        self._worker_snapshot_path: Optional[str] = None
        self._worker_status_path: Optional[str] = None
        self._worker_log_path: Optional[str] = None
        self._pending_rescan = False

    def scan_once(self):
        with self._scan_lock:
            self.status.index_ready = False
            snapshot_path = self._snapshot_path()
            snapshot_db = LocalSearchDB(snapshot_path, logger=self.logger, journal_mode="delete")
            status = _scan_to_db(self.config, snapshot_db, self.logger)
            try:
                snapshot_db.close_all()
            except Exception:
                try:
                    snapshot_db.close()
                except Exception:
                    pass
            try:
                self.db.swap_db_file(snapshot_path)
                self.status.scan_started_ts = status.get("scan_started_ts", 0)
                self.status.scan_finished_ts = status.get("scan_finished_ts", 0)
                self.status.scanned_files = status.get("scanned_files", 0)
                self.status.indexed_files = status.get("indexed_files", 0)
                self.status.symbols_extracted = status.get("symbols_extracted", 0)
                self.status.errors = status.get("errors", 0)
                self.status.index_version = status.get("index_version", "")
                self.status.index_ready = True
            except Exception as e:
                self.status.errors += 1
                self.status.last_error = str(e)
                if self.logger:
                    self.logger.error(f"Snapshot swap failed: {e}")

    def stop(self):
        self._stop_event.set()
        if self._worker_proc and self._worker_proc.is_alive():
            try:
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=2.0)
            except Exception:
                pass
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
    
    def run_forever(self):
        next_due = time.time()
        while not self._stop_event.is_set():
            self._finalize_worker_if_done()
            now = time.time()
            if self._rescan_event.is_set() or now >= next_due:
                self._rescan_event.clear()
                self._start_worker_scan()
                next_due = now + self.config.scan_interval_seconds
            time.sleep(0.2)

    def request_rescan(self):
        self._rescan_event.set()

    def index_file(self, _path: str):
        self.request_rescan()

    def _enqueue_fsevent(self, _evt: Any) -> None:
        self.request_rescan()

    def _snapshot_path(self) -> str:
        base = getattr(self.db, "db_path", "") or ""
        if base in ("", ":memory:"):
            tmp_dir = os.path.join(tempfile.gettempdir(), "sari_snapshots")
            os.makedirs(tmp_dir, exist_ok=True)
            base = os.path.join(tmp_dir, "index.db")
        return f"{base}.snapshot.{int(time.time() * 1000)}"

    def _serialize_config(self) -> Dict[str, Any]:
        data = dict(self.config.__dict__)
        return data

    def _start_worker_scan(self) -> None:
        if self._worker_proc and self._worker_proc.is_alive():
            self._pending_rescan = True
            return
        self.status.index_ready = False
        self.status.last_error = ""
        self._worker_snapshot_path = self._snapshot_path()
        self._worker_status_path = f"{self.db.db_path}.snapshot.status.json"
        self._worker_log_path = f"{self.db.db_path}.snapshot.log"
        cfg = self._serialize_config()
        ctx = multiprocessing.get_context("spawn")
        self._worker_proc = ctx.Process(
            target=_worker_build_snapshot,
            args=(cfg, self._worker_snapshot_path, self._worker_status_path, self._worker_log_path),
            daemon=True
        )
        self._worker_proc.start()

    def _finalize_worker_if_done(self) -> None:
        if not self._worker_proc:
            return
        if self._worker_proc.is_alive():
            return
        exitcode = self._worker_proc.exitcode
        self._worker_proc = None
        status_path = self._worker_status_path
        snapshot_path = self._worker_snapshot_path
        self._worker_status_path = None
        self._worker_snapshot_path = None
        if not status_path or not snapshot_path or not os.path.exists(status_path):
            self.status.errors += 1
            self.status.last_error = "worker status missing"
            return
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = f"worker status read failed: {e}"
            return
        if not payload.get("ok"):
            self.status.errors += 1
            self.status.last_error = payload.get("error", "worker failed")
            return
        if exitcode not in (0, None):
            self.status.errors += 1
            self.status.last_error = f"worker exit {exitcode}"
            return
        status = payload.get("status", {})
        try:
            self.db.swap_db_file(snapshot_path)
            self.status.scan_started_ts = status.get("scan_started_ts", 0)
            self.status.scan_finished_ts = status.get("scan_finished_ts", 0)
            self.status.scanned_files = status.get("scanned_files", 0)
            self.status.indexed_files = status.get("indexed_files", 0)
            self.status.symbols_extracted = status.get("symbols_extracted", 0)
            self.status.errors = status.get("errors", 0)
            self.status.index_version = status.get("index_version", "")
            self.status.index_ready = True
        except Exception as e:
            self.status.errors += 1
            self.status.last_error = str(e)
            if self.logger:
                self.logger.error(f"Snapshot swap failed: {e}")
        if self._pending_rescan:
            self._pending_rescan = False
            self._start_worker_scan()

class IndexStatus:
    def __init__(self):
        self.index_ready = False
        self.indexed_files = 0
        self.symbols_extracted = 0
        self.scan_started_ts = 0
        self.scan_finished_ts = 0
        self.scanned_files = 0
        self.errors = 0
        self.index_version = ""
        self.last_error = ""

    def to_meta(self) -> dict:
        return {
            "index_ready": bool(self.index_ready),
            "indexed_files": int(self.indexed_files or 0),
            "scanned_files": int(self.scanned_files or 0),
            "index_errors": int(self.errors or 0),
            "symbols_extracted": int(self.symbols_extracted or 0),
            "index_version": self.index_version or "",
            "last_error": self.last_error or "",
            "scan_started_ts": int(self.scan_started_ts or 0),
            "scan_finished_ts": int(self.scan_finished_ts or 0),
        }
