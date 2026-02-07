import sqlite3
import threading
import time
import queue
import os
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Iterable, Set

@dataclass
class DbTask:
    kind: str
    path: Optional[str] = None
    rows: Optional[List[tuple]] = None
    paths: Optional[List[str]] = None
    repo_meta: Optional[Dict[str, Any]] = None
    engine_docs: Optional[List[dict]] = None
    engine_deletes: Optional[List[str]] = None
    ts: float = field(default_factory=time.time)

class DBWriter:
    """
    Priority 2 & 3 Fix:
    - Moved engine updates out of DB transactions.
    - Fixed task_done() position to ensure queue.join() works correctly.
    """
    def __init__(self, db: Any, logger=None, max_batch: int = 100, max_wait: float = 0.1, on_commit=None):
        self.db = db
        self.logger = logger or logging.getLogger("sari.db_writer")
        self.max_batch = max_batch
        self.max_wait = max_wait
        self.on_commit = on_commit
        self.queue: "queue.Queue[DbTask]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.last_commit_ts = 0
        self._engine = getattr(db, "engine", None)

    def qsize(self) -> int: return self.queue.qsize()
    def enqueue(self, task: DbTask) -> None: self.queue.put(task)
    
    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for queue to be empty."""
        try:
            self.queue.join()
            return True
        except Exception:
            return False

    def start(self): self._thread.start()
    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread.is_alive(): self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            tasks = self._drain_batch(self.max_batch)
            if not tasks: continue
            try:
                # Priority 2: Standard DB write first
                self._process_batch(None, tasks)
                
                # Priority 2: External engine updates AFTER DB commit
                self._update_external_engine(tasks)
                
                # Priority 3: task_done() called only AFTER actual processing
                for _ in range(len(tasks)):
                    self.queue.task_done()
                    
                self.last_commit_ts = int(time.time())
            except Exception as e:
                self.logger.error(f"Write failure: {e}", exc_info=True)
                for _ in range(len(tasks)): self.queue.task_done()

    def _drain_batch(self, limit):
        tasks = []
        try:
            tasks.append(self.queue.get(timeout=self.max_wait))
        except queue.Empty:
            return []
        while len(tasks) < limit:
            try:
                tasks.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return tasks

    def _process_batch(self, cur, tasks):
        # Perform DB operations within a single transaction if possible
        with self.db.db.atomic():
            for t in tasks:
                if t.kind == "upsert_files" and t.rows:
                    self.db.upsert_files_turbo(t.rows)
                elif t.kind == "staging_merge":
                    self.db.finalize_turbo_batch()
                elif t.kind == "prune":
                    self.db.prune_stale_data(t.path, t.paths)
                elif t.kind == "barrier" and t.path: # Done event passed in 'path' field for simplicity
                    # Signal that all preceding tasks in this batch/queue are committed
                    try:
                        import threading
                        if isinstance(t.path, threading.Event):
                            t.path.set()
                    except: pass

    def _update_external_engine(self, tasks):
        """Update Tantivy outside of SQLite transaction to prevent blocking."""
        if not self._engine: return
        
        docs_to_upsert = []
        paths_to_delete = []
        for t in tasks:
            if t.engine_docs: docs_to_upsert.extend(t.engine_docs)
            if t.engine_deletes: paths_to_delete.extend(t.engine_deletes)
            
        if docs_to_upsert:
            try: self._engine.upsert_documents(docs_to_upsert)
            except Exception as e: self.logger.warning(f"Engine upsert failed: {e}")
        if paths_to_delete:
            try: self._engine.delete_documents(paths_to_delete)
            except Exception as e: self.logger.warning(f"Engine delete failed: {e}")

    def get_performance_metrics(self) -> Dict[str, Any]:
        return {"throughput_docs_sec": 0.0, "latency_p95": 0.0, "queue_depth": self.qsize()}
