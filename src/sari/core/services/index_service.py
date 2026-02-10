import time
from typing import Any, Dict

from sari.core.queue_pipeline import FsEvent, FsEventKind


class IndexService:
    def __init__(self, indexer: Any):
        self.indexer = indexer

    def _ensure_available(self) -> Dict[str, Any]:
        from sari.mcp.tools.protocol import ErrorCode

        if not self.indexer:
            return {"ok": False, "code": ErrorCode.INTERNAL, "message": "indexer not available"}
        if not getattr(self.indexer, "indexing_enabled", True):
            mode = getattr(self.indexer, "indexer_mode", "off")
            code = ErrorCode.ERR_INDEXER_DISABLED if mode == "off" else ErrorCode.ERR_INDEXER_FOLLOWER
            return {
                "ok": False,
                "code": code,
                "message": "Indexer is not available in follower/off mode",
                "data": {"mode": mode},
            }
        return {"ok": True}

    def scan_once(self) -> Dict[str, Any]:
        chk = self._ensure_available()
        if not chk.get("ok"):
            return chk

        self.indexer.scan_once()
        deadline = time.time() + 8.0
        stable_rounds = 0
        while time.time() < deadline:
            depths = self.indexer.get_queue_depths() if hasattr(self.indexer, "get_queue_depths") else {}
            fair_q = int(depths.get("fair_queue", 0))
            priority_q = int(depths.get("priority_queue", 0))
            db_q = int(depths.get("db_writer", 0))
            if fair_q == 0 and priority_q == 0 and db_q == 0:
                stable_rounds += 1
                if stable_rounds >= 3:
                    break
            else:
                stable_rounds = 0
            time.sleep(0.1)

        try:
            if hasattr(self.indexer, "storage") and hasattr(self.indexer.storage, "writer"):
                self.indexer.storage.writer.flush(timeout=2.0)
        except Exception:
            pass

        scanned = 0
        indexed = 0
        try:
            scanned = int(self.indexer.status.scanned_files or 0)
            indexed = int(self.indexer.status.indexed_files or 0)
        except Exception:
            pass

        return {"ok": True, "scanned_files": scanned, "indexed_files": indexed}

    def rescan(self) -> Dict[str, Any]:
        from sari.mcp.tools.protocol import ErrorCode

        chk = self._ensure_available()
        if not chk.get("ok"):
            return chk

        if hasattr(self.indexer, "request_rescan"):
            self.indexer.request_rescan()
        elif hasattr(self.indexer, "scan_once"):
            self.indexer.scan_once()
        else:
            return {"ok": False, "code": ErrorCode.INTERNAL, "message": "indexer does not support rescan"}

        return {"ok": True}

    def index_file(self, fs_path: str) -> Dict[str, Any]:
        from sari.mcp.tools.protocol import ErrorCode

        chk = self._ensure_available()
        if not chk.get("ok"):
            return chk

        try:
            evt = FsEvent(kind=FsEventKind.MODIFIED, path=fs_path, dest_path=None, ts=time.time())
            self.indexer._enqueue_fsevent(evt)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "code": ErrorCode.INTERNAL, "message": str(e)}
