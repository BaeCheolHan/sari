import time
import logging
from pathlib import Path
from typing import Optional
from sari.core.indexer.scanner import Scanner
from sari.core.indexer.worker import IndexWorker
from sari.core.indexer.db_writer import DBWriter

logger = logging.getLogger("sari.indexer")

class Indexer:
    def __init__(self, cfg, db):
        self.cfg = cfg
        self.db = db
        self.writer = DBWriter(db)
        self.worker = IndexWorker(cfg, db, logger, None)
        self.scanner = Scanner(cfg)

    def scan_once(self):
        """Perform a full scan and block until everything is committed to DB and Engine."""
        logger.info("🚀 Starting full scan...")
        start_ts = int(time.time())
        
        # 1. Preload metadata to avoid N+1 queries
        self.db.preload_metadata()
        
        # 2. Scan and process
        for root_id, root_path in self.scanner.get_active_roots():
            for file_path, st in self.scanner.walk(root_path):
                res = self.worker.process_file_task(
                    Path(root_path), Path(file_path), st, start_ts, time.time(), False, root_id=root_id
                )
                if res:
                    self.writer.enqueue(res)
        
        # 3. CRITICAL: Finalize all batches (DB + Search Engine)
        self.writer.finalize()
        
        # 4. Prune stale records
        self.db.prune_stale_files(start_ts)
        logger.info("✅ Scan complete and synchronized.")

    def start_watching(self):
        """Future: Real-time watchdog integration"""
        pass