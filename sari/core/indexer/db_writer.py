import sqlite3
import threading
import time
import queue
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Iterable

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
    snippet_rows: Optional[List[tuple]] = None
    context_rows: Optional[List[tuple]] = None
    failed_rows: Optional[List[tuple]] = None
    failed_paths: Optional[List[str]] = None

class DBWriter:
    def __init__(self, db: Any, logger=None, max_batch: int = 50, max_wait: float = 0.2, latency_cb=None, event_bus=None, on_commit=None):
        self.db = db
        self.logger = logger
        self.max_batch = max_batch
        self.max_wait = max_wait
        self.latency_cb = latency_cb
        self.event_bus = event_bus
        self.on_commit = on_commit # L2 캐시 삭제를 위한 콜백
        self.queue: "queue.Queue[DbTask]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.last_commit_ts = 0

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def flush(self, timeout: float = 5.0) -> None:
        """Wait for pending tasks to be committed."""
        if not self._thread.is_alive():
            return
        start = time.time()
        while time.time() - start < timeout:
            if self.queue.empty():
                break
            time.sleep(0.05)

    def enqueue(self, task: DbTask) -> None:
        self.queue.put(task)

    def qsize(self) -> int:
        return self.queue.qsize()

    def _run(self) -> None:
        self.db.register_writer_thread(threading.get_ident())
        conn = self.db._write
        cur = conn.cursor()
        
        coordinator = getattr(self.db, "coordinator", None)

        try:
            while not self._stop.is_set() or not self.queue.empty():
                is_throttled = coordinator.should_throttle_indexing() if coordinator else False
                if is_throttled:
                    current_max_batch = 1
                else:
                    qsize = self.queue.qsize()
                    current_max_batch = self.max_batch if qsize >= self.max_batch else max(1, qsize)
                
                tasks = self._drain_batch(current_max_batch)
                if not tasks:
                    continue

                try:
                    cur.execute("BEGIN")
                    stats = self._process_batch(cur, tasks)
                    conn.commit()
                    self.last_commit_ts = int(time.time())
                    
                    if self.on_commit and stats.get("files_paths"):
                        self.on_commit(stats["files_paths"])
                except Exception as e:
                    try: conn.rollback()
                    except: pass
                    if self.logger: self.logger.log_error(f"Batch failed, retrying individually: {e}")
                    
                    # --- 부분 실패 대응: 개별 재시도 ---
                    for single_task in tasks:
                        try:
                            cur.execute("BEGIN")
                            single_stats = self._process_batch(cur, [single_task])
                            conn.commit()
                            if self.on_commit and single_stats.get("files_paths"):
                                self.on_commit(single_stats["files_paths"])
                        except Exception as se:
                            try: conn.rollback()
                            except: pass
                            if self.logger: self.logger.log_error(f"Single task failed: {se}")
        finally:
            self.db.register_writer_thread(None)

    def _drain_batch(self, batch_limit: int) -> List[DbTask]:
        tasks: List[DbTask] = []
        try:
            first = self.queue.get(timeout=self.max_wait)
            tasks.append(first)
            self.queue.task_done()
        except queue.Empty:
            return tasks
        while len(tasks) < batch_limit:
            try:
                t = self.queue.get_nowait()
                tasks.append(t)
                self.queue.task_done()
            except queue.Empty:
                break
        return tasks

    def _process_batch(self, cur: sqlite3.Cursor, tasks: List[DbTask]) -> Dict[str, Any]:
        commit_ts = int(time.time())
        delete_paths: set[str] = set()
        upsert_files_rows: List[tuple] = []
        upsert_symbols_rows: List[tuple] = []
        upsert_relations_rows: List[tuple] = []
        update_last_seen_paths: List[str] = []
        repo_meta_tasks: List[dict] = []
        engine_docs: List[dict] = []
        engine_deletes: List[str] = []
        latency_samples: List[float] = []
        snippet_rows: List[tuple] = []
        context_rows: List[tuple] = []
        failed_rows: List[tuple] = []
        failed_clear_paths: List[str] = []

        for t in tasks:
            if t.kind == "delete_path" and t.path:
                delete_paths.add(t.path)
                if t.engine_deletes: engine_deletes.extend(t.engine_deletes)
                latency_samples.append(time.time() - t.ts)
            elif t.kind == "upsert_files" and t.rows:
                upsert_files_rows.extend(t.rows)
                if t.engine_docs: engine_docs.extend(t.engine_docs)
                latency_samples.append(time.time() - t.ts)
            elif t.kind == "upsert_symbols" and t.rows: upsert_symbols_rows.extend(t.rows)
            elif t.kind == "upsert_relations" and t.rows: upsert_relations_rows.extend(t.rows)
            elif t.kind == "update_last_seen" and t.paths: update_last_seen_paths.extend(t.paths)
            elif t.kind == "upsert_repo_meta" and t.repo_meta: repo_meta_tasks.append(t.repo_meta)
            elif t.kind == "upsert_snippets" and t.snippet_rows: snippet_rows.extend(t.snippet_rows)
            elif t.kind == "upsert_contexts" and t.context_rows: context_rows.extend(t.context_rows)
            elif t.kind == "dlq_upsert" and t.failed_rows: failed_rows.extend(t.failed_rows)
            elif t.kind == "dlq_clear" and t.failed_paths: failed_clear_paths.extend(t.failed_paths)

        # De-duplicate and apply deletions
        for p in delete_paths:
            try:
                cur.execute("DELETE FROM files WHERE path = ?", (p,))
            except: pass

        if upsert_files_rows:
            self.db.upsert_files_tx(cur, upsert_files_rows)
            for r in upsert_files_rows:
                self.db.mark_embeddings_stale(cur, r[2], r[0], r[7])
        
        if upsert_symbols_rows: 
            self.db.upsert_symbols_tx(cur, upsert_symbols_rows)
        if upsert_relations_rows: self.db.upsert_relations_tx(cur, upsert_relations_rows)
        
        if repo_meta_tasks:
            for m in repo_meta_tasks:
                self.db.upsert_repo_meta_tx(cur, m.get("repo_name", ""), m.get("tags", ""), m.get("domain", ""), m.get("description", ""), int(m.get("priority", 0)))
        
        if snippet_rows: self.db.upsert_snippet_tx(cur, snippet_rows)
        if context_rows: self.db.upsert_context_tx(cur, context_rows)
        if failed_rows: self.db.upsert_failed_tasks_tx(cur, failed_rows)
        if failed_clear_paths: self.db.clear_failed_tasks_tx(cur, failed_clear_paths)

        engine = getattr(self.db, "engine", None)
        if engine:
            try:
                if engine_docs and hasattr(engine, "upsert_documents"): engine.upsert_documents(engine_docs)
                if engine_deletes and hasattr(engine, "delete_documents"): engine.delete_documents(engine_deletes)
            except Exception as e:
                if self.logger: self.logger.log_error(f"engine update failed: {e}")

        if self.latency_cb and latency_samples:
            for s in latency_samples: self.latency_cb(s)
        return {
            "ts": commit_ts,
            "files": len(upsert_files_rows),
            "files_paths": [r[0] for r in upsert_files_rows],
            "symbols": len(upsert_symbols_rows),
            "relations": len(upsert_relations_rows),
            "snippets": len(snippet_rows),
            "contexts": len(context_rows),
            "deleted": len(delete_paths),
            "failed": len(failed_rows),
        }