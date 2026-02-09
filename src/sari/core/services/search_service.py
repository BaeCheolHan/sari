from typing import Any, Dict, Tuple, List
from sari.core.models import SearchOptions, SearchHit
from sari.core.engine_runtime import EngineError


class SearchService:
    def __init__(self, db: Any, engine: Any = None, indexer: Any = None):
        self.db = db
        self.engine = engine
        self.indexer = indexer

    def _rrf_fusion(self, keyword_hits: List[SearchHit], semantic_hits: List[SearchHit], k: int = 60) -> List[SearchHit]:
        """Merges results using Reciprocal Rank Fusion (RRF) algorithm."""
        scores: Dict[str, float] = {}
        hit_map: Dict[str, SearchHit] = {}
        
        # Process Keyword ranks
        for i, h in enumerate(keyword_hits):
            scores[h.path] = scores.get(h.path, 0.0) + (1.0 / (k + i + 1))
            hit_map[h.path] = h
            
        # Process Semantic ranks
        for i, h in enumerate(semantic_hits):
            scores[h.path] = scores.get(h.path, 0.0) + (1.0 / (k + i + 1))
            if h.path not in hit_map:
                hit_map[h.path] = h
            else:
                # If duplicate, mark as hybrid and combine scores
                hit_map[h.path].hit_reason += f" + Semantic"
                
        # Sort by fused score
        sorted_paths = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        
        results = []
        for path in sorted_paths:
            h = hit_map[path]
            # Normalize score for output (mapping RRF score to human-readable scale)
            h.score = scores[path] * 1000.0 
            results.append(h)
        return results

    def search(self, opts: SearchOptions) -> Tuple[List[SearchHit], Dict[str, Any]]:
        """Unified hybrid search with RRF fusion."""
        engine = self.engine
        try:
            # 1. Primary Keyword Search
            keyword_hits, meta = ([], {})
            if engine:
                keyword_hits, meta = engine.search_v2(opts)
            
            # 2. Semantic Search (if vector provided in metadata or explicitly)
            semantic_hits = []
            query_vector = getattr(opts, "query_vector", None)
            if query_vector and hasattr(self.db, "search_repo"):
                try:
                    semantic_hits = self.db.search_repo().search_semantic(
                        query_vector, 
                        limit=opts.limit,
                        root_ids=opts.root_ids
                    )
                except Exception: pass
            
            # 3. Hybrid Fusion
            if semantic_hits:
                fused_hits = self._rrf_fusion(keyword_hits, semantic_hits)
                meta["engine"] = "hybrid-rrf"
                return fused_hits[:opts.limit], meta
            
            return keyword_hits, meta

        except EngineError: raise
        except Exception as exc:
            # Fallback logic
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
