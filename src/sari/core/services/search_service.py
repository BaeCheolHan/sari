from collections.abc import Mapping
from typing import TypeAlias

from sari.core.models import SearchOptions, SearchHit
from sari.core.engine_runtime import EngineError

SearchMeta: TypeAlias = dict[str, object]
SearchResult: TypeAlias = tuple[list[SearchHit], SearchMeta]


class SearchService:
    """
    RRF(Reciprocal Rank Fusion) 알고리즘을 사용한 하이브리드 검색 서비스입니다.
    키워드 검색(Rust 엔진)과 시맨틱 검색(임베딩) 결과를 병합하여 최적의 검색 결과를 제공합니다.
    """

    def __init__(self, db: object, engine: object = None, indexer: object = None):
        """
        Args:
            db: 데이터베이스 접근 객체
            engine: 검색 엔진 객체 (Tantivy/Rust 등)
            indexer: 인덱서 객체 (상태 확인용)
        """
        self.db = db
        self.engine = engine
        self.indexer = indexer

    def _rrf_fusion(
            self,
            keyword_hits: list[SearchHit],
            semantic_hits: list[SearchHit],
            k: int = 60) -> list[SearchHit]:
        """
        RRF 알고리즘을 사용하여 검색 결과를 병합합니다.
        순위 기반 점수 합산 방식으로, 서로 다른 스코어 체계를 가진 검색 결과들을 효과적으로 결합합니다.

        Args:
            keyword_hits: 키워드 검색 결과 목록
            semantic_hits: 시맨틱 검색 결과 목록
            k: 랭크 상수 (기본값 60)

        Returns:
            병합되고 정렬된 검색 결과 목록
        """
        scores: dict[str, float] = {}
        hit_map: dict[str, SearchHit] = {}

        # 키워드 검색 결과 순위 처리
        for i, h in enumerate(keyword_hits):
            scores[h.path] = scores.get(h.path, 0.0) + (1.0 / (k + i + 1))
            hit_map[h.path] = h

        # 시맨틱 검색 결과 순위 처리
        for i, h in enumerate(semantic_hits):
            scores[h.path] = scores.get(h.path, 0.0) + (1.0 / (k + i + 1))
            if h.path not in hit_map:
                hit_map[h.path] = h
            else:
                # 중복되는 경우 하이브리드 매칭으로 표시
                hit_map[h.path].hit_reason += " + Semantic"

        # 퓨전 점수 기준 내림차순 정렬
        sorted_paths = sorted(
            scores.keys(),
            key=lambda x: scores[x],
            reverse=True)

        results = []
        for path in sorted_paths:
            h = hit_map[path]
            # 점수 정규화 (RRF 점수를 보기 좋은 1000점 만점 스케일로 변환)
            h.score = scores[path] * 1000.0
            results.append(h)
        return results

    @staticmethod
    def _coerce_meta(meta: object) -> SearchMeta:
        return dict(meta) if isinstance(meta, Mapping) else {}

    @staticmethod
    def _coerce_hits(raw_hits: object) -> list[SearchHit]:
        if not isinstance(raw_hits, list):
            return []
        out: list[SearchHit] = []
        for item in raw_hits:
            if isinstance(item, SearchHit):
                out.append(item)
            elif isinstance(item, Mapping):
                try:
                    out.append(SearchHit.from_dict(dict(item)))
                except Exception:
                    continue
        return out

    def search(
            self, opts: SearchOptions) -> SearchResult:
        """
        통합 하이브리드 검색을 수행합니다.
        1. 키워드 검색 수행
        2. (가능한 경우) 시맨틱 검색 수행
        3. 결과 병합 및 반환
        """
        engine = self.engine
        try:
            # 1. 1차 키워드 검색
            keyword_hits: list[SearchHit] = []
            meta: SearchMeta = {}
            if engine:
                search_fn = getattr(engine, "search", None)
                if callable(search_fn):
                    raw_hits, raw_meta = search_fn(opts)
                    keyword_hits = self._coerce_hits(raw_hits)
                    meta = self._coerce_meta(raw_meta)

            # 2. 2차 시맨틱 검색 (메타데이터에 쿼리 벡터가 있거나 명시된 경우)
            semantic_hits: list[SearchHit] = []
            query_vector = getattr(opts, "query_vector", None)
            if query_vector and hasattr(self.db, "search_repo"):
                try:
                    repo = self.db.search_repo()
                    if hasattr(repo, "search_semantic"):
                        raw_semantic_hits = repo.search_semantic(
                            query_vector,
                            limit=opts.limit,
                            root_ids=opts.root_ids,
                        )
                        semantic_hits = self._coerce_hits(raw_semantic_hits)
                except Exception:
                    pass

            # 3. 하이브리드 퓨전 (병합)
            if semantic_hits:
                fused_hits = self._rrf_fusion(keyword_hits, semantic_hits)
                meta["engine"] = "hybrid-rrf"
                return fused_hits[:opts.limit], meta

            return keyword_hits, meta

        except EngineError:
            raise
        except Exception as exc:
            # 엔진 에러 발생 시 폴백 로직 (L2 검색 등)
            fallback_hits = []
            fallback_meta: SearchMeta = {
                "partial": True,
                "db_health": "error",
                "db_error": str(exc),
                "engine": "fallback",
            }
            if engine and hasattr(
                    engine,
                    "search_engine") and hasattr(
                    engine.search_engine,
                    "search_l2_only"):
                fallback_hits, fallback_meta = engine.search_engine.search_l2_only(
                    opts)
            elif engine and hasattr(engine, "search_l2_only"):
                fallback_hits, fallback_meta = engine.search_l2_only(opts)
            return fallback_hits, fallback_meta

    def index_meta(self) -> SearchMeta:
        """인덱서의 상태 메타데이터를 반환합니다."""
        if self.indexer is None or not getattr(self.indexer, "status", None):
            return {}
        st = self.indexer.status
        if hasattr(st, "to_meta"):
            raw_meta = st.to_meta()
            if isinstance(raw_meta, Mapping):
                return dict(raw_meta)
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
