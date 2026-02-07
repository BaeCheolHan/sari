import queue
import threading
import logging
import time
from typing import List, Dict, Any

logger = logging.getLogger("sari.db_writer")

class DBWriter:
    def __init__(self, db):
        self.db = db
        self.queue = queue.Queue(maxsize=2000)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enqueue(self, task: Dict[str, Any]):
        self.queue.put(task)

    def finalize(self):
        """Block until all tasks are processed and committed to search engine."""
        while not self.queue.empty():
            time.sleep(0.1)
        
        # Priority Fix: Explicitly commit to SQLite and Search Engine
        self.db.finalize_turbo_batch()
        # If DB has a search engine (Tantivy), ensure it's committed
        if hasattr(self.db, "engine") and self.db.engine:
            try:
                self.db.engine.commit()
                logger.debug("Tantivy engine committed successfully.")
            except Exception as e:
                logger.error(f"Failed to commit Tantivy: {e}")

    def _run(self):
        batch = []
        last_flush = time.time()
        
        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                task = self.queue.get(timeout=0.5)
                batch.append(task)
            except queue.Empty:
                if batch: self._flush(batch); batch = []
                continue

            if len(batch) >= 100 or (time.time() - last_flush > 2.0):
                self._flush(batch)
                batch = []
                last_flush = time.time()

    def _flush(self, batch: List[Dict[str, Any]]):
        try:
            # Batch upsert to SQLite
            self.db.upsert_files_turbo(batch)
            # Sync to Search Engine
            if hasattr(self.db, "engine") and self.db.engine:
                docs = [t["engine_doc"] for t in batch if "engine_doc" in t]
                if docs: self.db.engine.add_documents(docs)
        except Exception as e:
            logger.error(f"Flush error: {e}")

    def stop(self):
        self._stop_event.set()
        self._thread.join()