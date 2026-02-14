import threading
import time
import queue
import os
import logging
from dataclasses import dataclass, field, replace
from typing import Protocol, Dict, List, Optional

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
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
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
    rows: Optional[List[object]] = None
    paths: Optional[List[str]] = None
    repo_meta: Optional[Dict[str, object]] = None
    engine_docs: Optional[List[dict[str, object]]] = None
    engine_deletes: Optional[List[str]] = None
    attempts: int = 0
    ts: float = field(default_factory=time.time)


class _EngineProtocol(Protocol):
    def upsert_documents(self, docs: List[dict[str, object]]) -> None: ...
    def delete_documents(self, doc_ids: List[str]) -> None: ...


class _DBProtocol(Protocol):
    engine: Optional[_EngineProtocol]

    def upsert_files_tx(self, cur: object, rows: List[object]) -> None: ...
    def upsert_files_staging(self, cur: object, rows: List[object]) -> None: ...
    def finalize_turbo_batch(self) -> None: ...
    def update_last_seen_tx(self, cur: object, paths: Optional[List[str]], ts: int) -> None: ...
    def delete_path_tx(self, cur: object, path: str) -> None: ...


class DBWriter:
    def __init__(
            self,
            db: _DBProtocol,
            logger=None,
            max_batch: int = 100,
            max_wait: float = 0.1,
            on_commit=None,
            max_retries: int = 1):
        self.db = db
        self.logger = logger or logging.getLogger("sari.db_writer")
        self.max_batch = max_batch
        self.max_wait = max_wait
        self.on_commit = on_commit
        self.max_retries = max(0, int(max_retries))
        self.queue: "queue.Queue[DbTask]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.last_commit_ts = 0
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()

        try:
            import fcntl
            self.fcntl = fcntl
        except Exception:
            self.fcntl = None

        from collections import deque
        self._latency_window = deque(maxlen=100)
        self._throughput_window = deque(maxlen=20)

    def qsize(self) -> int: return self.queue.qsize()
    def enqueue(self, task: DbTask) -> None: self.queue.put(task)

    def flush(self, timeout: float = 5.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            with self._in_flight_lock:
                in_flight = self._in_flight
            if self.queue.empty() and in_flight == 0:
                return True
            time.sleep(0.01)
        with self._in_flight_lock:
            in_flight = self._in_flight
        return self.queue.empty() and in_flight == 0

    def start(self): self._thread.start()

    def stop(self, timeout=2.0):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set() or not self.queue.empty() or self._in_flight > 0:
            tasks = self._drain_batch(self.max_batch)
            if not tasks:
                continue
            try:
                self._process_batch(None, tasks)
            except Exception as e:
                self._requeue_failed_tasks(tasks, e)
            finally:
                self._mark_tasks_done(tasks)

    def _requeue_failed_tasks(self, tasks: List[DbTask], err: Exception) -> None:
        retry_tasks: List[DbTask] = []
        dropped = 0
        for task in tasks:
            if int(getattr(task, "attempts", 0)) < self.max_retries:
                retry_tasks.append(replace(task, attempts=int(getattr(task, "attempts", 0)) + 1))
            else:
                dropped += 1
        if retry_tasks:
            for task in retry_tasks:
                self.enqueue(task)
            self.logger.warning(
                "Write failure; requeued batch tasks retry=%s count=%s error=%s",
                retry_tasks[0].attempts,
                len(retry_tasks),
                err,
            )
        if dropped:
            self.logger.error("Write failure; dropped tasks=%s error=%s", dropped, err)

    def _drain_batch(self, limit: int) -> List[DbTask]:
        tasks: List[DbTask] = []
        try:
            tasks.append(self.queue.get(timeout=self.max_wait))
        except queue.Empty:
            return []
        while len(tasks) < limit:
            try:
                tasks.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if tasks:
            with self._in_flight_lock:
                self._in_flight += len(tasks)
        return tasks

    def _mark_tasks_done(self, tasks: List[DbTask]) -> None:
        if not tasks:
            return
        for _ in tasks:
            try:
                self.queue.task_done()
            except ValueError:
                # In case task_done is called too many times, keep running
                # safely.
                pass
        with self._in_flight_lock:
            self._in_flight = max(0, self._in_flight - len(tasks))

    def _process_batch(self, cur: object, tasks: List[DbTask]) -> None:
        conn = None
        own_tx = False
        if cur is None:
            conn = getattr(self.db, "_write", None)
            if conn is not None and hasattr(conn, "cursor"):
                cur = conn.cursor()
                try:
                    if not bool(getattr(conn, "in_transaction", False)):
                        conn.execute("BEGIN IMMEDIATE TRANSACTION")
                        own_tx = True
                except Exception:
                    own_tx = False
        pending_engine_upserts: List[dict[str, object]] = []
        pending_engine_deletes: List[str] = []
        engine = getattr(self.db, "engine", None)
        try:
            for t in tasks:
                if t.kind == "upsert_files" and t.rows:
                    self.db.upsert_files_tx(cur, t.rows)
                    if t.engine_docs and engine:
                        pending_engine_upserts.extend(t.engine_docs)
                elif t.kind == "upsert_files_staging" and t.rows:
                    self.db.upsert_files_staging(cur, t.rows)
                elif t.kind == "staging_merge":
                    self.db.finalize_turbo_batch()
                elif t.kind == "update_last_seen":
                    self.db.update_last_seen_tx(cur, t.paths, int(time.time()))
                elif t.kind == "delete_path" and t.path:
                    self.db.delete_path_tx(cur, t.path)
                    if t.engine_deletes and engine:
                        pending_engine_deletes.extend(t.engine_deletes)

            if engine:
                has_pending = False
                if pending_engine_upserts:
                    try:
                        engine.upsert_documents(pending_engine_upserts, commit=False)
                    except TypeError:
                        engine.upsert_documents(pending_engine_upserts)
                    has_pending = True
                if pending_engine_deletes:
                    try:
                        engine.delete_documents(pending_engine_deletes, commit=False)
                    except TypeError:
                        engine.delete_documents(pending_engine_deletes)
                    has_pending = True
                if has_pending and hasattr(engine, "commit"):
                    engine.commit()

            if own_tx and conn is not None:
                conn.commit()
        except Exception:
            if own_tx and conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise

    def get_performance_metrics(self) -> Dict[str, float]:
        return {
            "throughput_docs_sec": 0.0,
            "latency_p95": 0.0,
            "queue_depth": float(self.qsize())}
