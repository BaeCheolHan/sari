import sqlite3
import threading
import time
import queue
import os
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Iterable, Set

try:
    import fcntl  # type: ignore
except Exception:
    fcntl = None

try:
    import msvcrt  # type: ignore
except Exception:
    msvcrt = None

class _WriteGate:
    """Cross-platform advisory lock for DB write access."""
    def __init__(self, db_path: str):
        self.lock_path = f"{db_path}.lock"
        self._fh = None

    def __enter__(self):
        # Use os.open with O_CREAT to ensure atomic creation/access
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o666)
        self._fh = os.fdopen(fd, "a+")
        if fcntl is not None:
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        elif msvcrt is not None:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh:
                if fcntl is not None:
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                elif msvcrt is not None:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            try:
                if self._fh:
                    self._fh.close()
            finally:
                self._fh = None

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
        
        try: import fcntl; self.fcntl = fcntl
        except: self.fcntl = None
        
        from collections import deque
        self._latency_window = deque(maxlen=100)
        self._throughput_window = deque(maxlen=20)

    def qsize(self) -> int: return self.queue.qsize()
    def enqueue(self, task: DbTask) -> None: self.queue.put(task)
    def flush(self, timeout: float = 5.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.queue.empty(): return True
            time.sleep(0.01)
        return self.queue.empty()

    def start(self): self._thread.start()
    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread.is_alive(): self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            tasks = self._drain_batch(self.max_batch)
            if not tasks: continue
            try:
                self._process_batch(None, tasks)
            except Exception as e:
                self.logger.error(f"Write failure: {e}")

    def _drain_batch(self, limit):
        tasks = []
        try: tasks.append(self.queue.get(timeout=self.max_wait)); self.queue.task_done()
        except queue.Empty: return []
        while len(tasks) < limit:
            try: tasks.append(self.queue.get_nowait()); self.queue.task_done()
            except queue.Empty: break
        return tasks

    def _process_batch(self, cur, tasks):
        for t in tasks:
            if t.kind == "upsert_files" and t.rows:
                self.db.upsert_files_tx(cur, t.rows)
                if t.engine_docs and hasattr(self.db, "engine") and self.db.engine:
                    self.db.engine.upsert_documents(t.engine_docs)
            elif t.kind == "upsert_files_staging" and t.rows: self.db.upsert_files_staging(cur, t.rows)
            elif t.kind == "staging_merge": self.db.finalize_turbo_batch()
            elif t.kind == "update_last_seen": self.db.update_last_seen_tx(cur, t.paths, int(time.time()))
            elif t.kind == "delete_path" and t.path:
                self.db.delete_path_tx(cur, t.path)
                if t.engine_deletes and hasattr(self.db, "engine") and self.db.engine:
                    self.db.engine.delete_documents(t.engine_deletes)

    def get_performance_metrics(self) -> Dict[str, Any]:
        return {"throughput_docs_sec": 0.0, "latency_p95": 0.0, "queue_depth": self.qsize()}
