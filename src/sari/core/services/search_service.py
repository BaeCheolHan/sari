from typing import Any, Dict, Tuple, List
from sari.core.models import SearchOptions, SearchHit
from sari.core.engine_runtime import EngineError


class SearchService:
    def __init__(self, db: Any, engine: Any = None, indexer: Any = None):
        self.db = db
        self.engine = engine
        self.indexer = indexer

    def search(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, Any]]:
        engine = self.engine
        try:
            if engine is None:
                return [], {}
            hits, meta = engine.search_v2(opts)
            return hits, meta
        except EngineError:
            raise
        except Exception as exc:
            # DB/엔진 장애 시 부분 결과라도 반환
            fallback_hits = []
            fallback_meta: Dict[str, Any] = {
                "partial": True,
                "db_health": "error",
                "db_error": str(exc),
                "engine": "fallback",
            }
            if engine and hasattr(engine, "search_engine") and hasattr(engine.search_engine, "search_l2_only"):
                fallback_hits, fallback_meta = engine.search_engine.search_l2_only(opts)
            elif engine and hasattr(engine, "search_l2_only"):
                fallback_hits, fallback_meta = engine.search_l2_only(opts)
            return fallback_hits, fallback_meta

    def index_meta(self) -> Dict[str, Any]:
        if self.indexer is None or not getattr(self.indexer, "status", None):
            return {}
        st = self.indexer.status
        if hasattr(st, "to_meta"):
            return st.to_meta()
        return {
            "index_ready": bool(getattr(st, "index_ready", False)),
            "indexed_files": int(getattr(st, "indexed_files", 0) or 0),
            "scanned_files": int(getattr(st, "scanned_files", 0) or 0),
            "index_errors": int(getattr(st, "errors", 0) or 0),
            "symbols_extracted": int(getattr(st, "symbols_extracted", 0) or 0),
            "index_version": getattr(st, "index_version", "") or "",
            "last_error": getattr(st, "last_error", "") or "",
            "scan_started_ts": int(getattr(st, "scan_started_ts", 0) or 0),
            "scan_finished_ts": int(getattr(st, "scan_finished_ts", 0) or 0),
        }
