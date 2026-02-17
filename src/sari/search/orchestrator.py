"""검색 오케스트레이터를 구현한다."""

from __future__ import annotations

from dataclasses import dataclass, field

from sari.core.models import SearchErrorDTO, SearchItemDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.search.candidate_search import CandidateSearchResultDTO, CandidateSearchService
from sari.search.error_policy import has_fatal_errors
from sari.search.hierarchy_scorer import HierarchyScorer
from sari.search.importance_scorer import ImportanceScorer
from sari.search.score_blender import ScoreBlender
from sari.search.symbol_resolve import SymbolResolveService
from sari.search.vector_reranker import VectorReranker


@dataclass(frozen=True)
class SearchMetaDTO:
    """검색 메타 정보를 표현한다."""

    candidate_count: int
    resolved_count: int
    candidate_source: str
    errors: list[SearchErrorDTO]
    fatal_error: bool
    degraded: bool
    error_count: int
    ranking_policy: str = "legacy"
    rrf_k: int = 0
    lsp_query_mode: str = "document_symbol"
    lsp_sync_mode: str = "did_open_did_change"
    lsp_fallback_used: bool = False
    lsp_fallback_reason: str | None = None
    importance_policy: str = "none"
    importance_weights: dict[str, float] | None = None
    importance_normalize_mode: str = "none"
    importance_max_boost: float = 0.0
    vector_enabled: bool = False
    vector_rerank_count: int = 0
    vector_applied_count: int = 0
    vector_skipped_count: int = 0
    vector_threshold: float = 0.0
    ranking_stage: str = "blend"
    blend_config_version: str = "v1"
    ranking_version: str = "v3-hierarchy"
    ranking_components_enabled: dict[str, bool] = field(
        default_factory=lambda: {"rrf": True, "importance": True, "vector": True, "hierarchy": True}
    )


@dataclass(frozen=True)
class RankingBlendConfigDTO:
    """RRF/importance/vector 결합 가중치 설정을 표현한다."""

    w_rrf: float = 0.55
    w_importance: float = 0.30
    w_vector: float = 0.15
    w_hierarchy: float = 0.15
    version: str = "v1"


@dataclass(frozen=True)
class SearchPipelineResult:
    """검색 파이프라인 결과를 표현한다."""

    items: list[SearchItemDTO]
    meta: SearchMetaDTO


class SearchOrchestrator:
    """후보 검색과 심볼 해석을 조합한다."""

    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        candidate_service: CandidateSearchService,
        symbol_service: SymbolResolveService,
        importance_scorer: ImportanceScorer | None = None,
        hierarchy_scorer: HierarchyScorer | None = None,
        vector_reranker: VectorReranker | None = None,
        blend_config: RankingBlendConfigDTO | None = None,
    ) -> None:
        """검색 구성요소를 주입한다."""
        self._workspace_repo = workspace_repo
        self._candidate_service = candidate_service
        self._symbol_service = symbol_service
        self._importance_scorer = importance_scorer
        self._hierarchy_scorer = hierarchy_scorer
        self._vector_reranker = vector_reranker
        self._rrf_k = 60
        self._blend_config = blend_config if blend_config is not None else RankingBlendConfigDTO()
        self._w_rrf = self._blend_config.w_rrf
        self._w_importance = self._blend_config.w_importance
        self._w_vector = self._blend_config.w_vector
        self._w_hierarchy = self._blend_config.w_hierarchy
        self._score_blender = ScoreBlender(
            rrf_k=self._rrf_k,
            w_rrf=self._w_rrf,
            w_importance=self._w_importance,
            w_vector=self._w_vector,
            w_hierarchy=self._w_hierarchy,
        )

    def search(self, query: str, limit: int, repo_root: str, resolve_symbols: bool = True) -> SearchPipelineResult:
        """질의어 기반 검색 결과를 반환한다."""
        workspaces = self._workspace_repo.list_all()
        filtered_workspaces = self._candidate_service.filter_workspaces_by_repo(workspaces, repo_root)
        candidate_result: CandidateSearchResultDTO = self._candidate_service.search(
            workspaces=filtered_workspaces,
            query=query,
            limit=limit,
        )
        candidates = candidate_result.candidates
        if resolve_symbols:
            resolved_items, errors = self._symbol_service.resolve(candidates=candidates, query=query, limit=limit)
            merged_errors = [*candidate_result.errors, *errors]
        else:
            resolved_items = []
            merged_errors = list(candidate_result.errors)
        fatal_error = has_fatal_errors(merged_errors)
        degraded = len(merged_errors) > 0
        error_count = len(merged_errors)
        candidate_items = [
            SearchItemDTO(
                item_type="file",
                repo=candidate.repo_root,
                relative_path=candidate.relative_path,
                score=candidate.score,
                source="candidate",
                name=None,
                kind=None,
                content_hash=candidate.file_hash,
                rrf_score=candidate.score,
                importance_score=0.0,
                base_rrf_score=candidate.score,
                importance_norm_score=0.0,
                vector_norm_score=0.0,
                vector_score=None,
                blended_score=candidate.score,
                final_score=candidate.score,
            )
            for candidate in candidates[:limit]
        ]

        if resolve_symbols:
            fused_items = self._score_blender.fuse_rrf(
                candidate_items=candidate_items,
                resolved_items=resolved_items,
                limit=limit,
            )
            resolved_count = len([item for item in fused_items if item.item_type != "file"])
            ranking_policy = "rrf_importance_vector_blend"
            rrf_k = self._rrf_k
        else:
            fused_items = candidate_items[:limit]
            resolved_count = 0
            ranking_policy = "candidate_only"
            rrf_k = 0

        if self._importance_scorer is not None:
            fused_items = self._importance_scorer.apply(items=fused_items, query=query)
        if self._hierarchy_scorer is not None:
            fused_items = self._hierarchy_scorer.apply(items=fused_items, query=query)
        vector_applied_count = 0
        vector_skipped_count = 0
        vector_threshold = 0.0
        if self._vector_reranker is not None:
            fused_items = self._vector_reranker.rerank(items=fused_items, query=query, limit=limit)
            stats = self._vector_reranker.last_stats
            vector_applied_count = stats.applied_count
            vector_skipped_count = stats.skipped_count
            vector_threshold = self._vector_reranker.config.min_similarity_threshold
        fused_items = self._score_blender.blend(fused_items, limit=limit)

        return SearchPipelineResult(
            items=fused_items,
            meta=SearchMetaDTO(
                candidate_count=len(candidates),
                resolved_count=resolved_count,
                candidate_source=candidate_result.source,
                errors=merged_errors,
                fatal_error=fatal_error,
                degraded=degraded,
                error_count=error_count,
                ranking_policy=ranking_policy,
                rrf_k=rrf_k,
                lsp_query_mode="document_symbol",
                importance_policy="sari_v1_hybrid_static" if self._importance_scorer is not None else "none",
                importance_weights=self._importance_scorer.weights.to_dict() if self._importance_scorer is not None else None,
                importance_normalize_mode=(
                    self._importance_scorer.policy.normalize_mode if self._importance_scorer is not None else "none"
                ),
                importance_max_boost=(
                    self._importance_scorer.policy.max_importance_boost if self._importance_scorer is not None else 0.0
                ),
                vector_enabled=self._vector_reranker.config.enabled if self._vector_reranker is not None else False,
                vector_rerank_count=(
                    min(len(fused_items), self._vector_reranker.config.candidate_k)
                    if self._vector_reranker is not None and self._vector_reranker.config.enabled
                    else 0
                ),
                vector_applied_count=vector_applied_count,
                vector_skipped_count=vector_skipped_count,
                vector_threshold=vector_threshold,
                lsp_sync_mode="did_open_did_change",
                lsp_fallback_used=False,
                lsp_fallback_reason=None,
                ranking_stage="blend",
                blend_config_version=self._blend_config.version,
                ranking_version="v3-hierarchy",
                ranking_components_enabled={
                    "rrf": True,
                    "importance": self._importance_scorer is not None,
                    "vector": self._vector_reranker is not None and self._vector_reranker.config.enabled,
                    "hierarchy": self._hierarchy_scorer is not None,
                },
            ),
        )
