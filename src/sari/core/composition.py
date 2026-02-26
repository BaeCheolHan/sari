"""애플리케이션 composition root 헬퍼를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sari.core.config import AppConfig, LspHubRuntimeConfigDTO, SearchRuntimeConfigDTO
from sari.db.migration import ensure_migrated
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.pipeline_stage_baseline_repository import PipelineStageBaselineRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.symbol_importance_repository import SymbolImportanceRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.vector_embedding_repository import VectorEmbeddingRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema

if TYPE_CHECKING:
    from sari.lsp.hub import LspHub
    from sari.search.candidate_search import CandidateSearchService
    from sari.search.hierarchy_scorer import HierarchyScorer
    from sari.search.importance_scorer import ImportanceScorer
    from sari.search.orchestrator import SearchOrchestrator
    from sari.search.symbol_resolve import SymbolResolveService
    from sari.search.vector_reranker import VectorIndexSink, VectorReranker


@dataclass(frozen=True)
class RepositoryBundle:
    """프로세스 전역에서 공유하는 repository 집합."""

    workspace_repo: WorkspaceRepository
    runtime_repo: RuntimeRepository
    daemon_registry_repo: DaemonRegistryRepository
    symbol_cache_repo: SymbolCacheRepository
    symbol_importance_repo: SymbolImportanceRepository
    file_repo: FileCollectionRepository
    enrich_queue_repo: FileEnrichQueueRepository
    body_repo: FileBodyRepository
    lsp_repo: LspToolDataRepository
    tool_layer_repo: ToolDataLayerRepository
    knowledge_repo: KnowledgeRepository
    readiness_repo: ToolReadinessRepository
    policy_repo: PipelinePolicyRepository
    control_state_repo: PipelineControlStateRepository
    event_repo: PipelineJobEventRepository
    error_event_repo: PipelineErrorEventRepository
    perf_repo: PipelinePerfRepository
    stage_baseline_repo: PipelineStageBaselineRepository
    quality_repo: PipelineQualityRepository
    language_probe_repo: LanguageProbeRepository
    lsp_matrix_repo: PipelineLspMatrixRepository
    repo_registry_repo: RepoRegistryRepository
    vector_repo: VectorEmbeddingRepository
    candidate_change_repo: CandidateIndexChangeRepository


@dataclass(frozen=True)
class SearchStackBundle:
    """search/collection wiring에 재사용되는 검색 스택 구성요소."""

    candidate_service: CandidateSearchService
    importance_scorer: ImportanceScorer
    hierarchy_scorer: HierarchyScorer
    vector_sink: VectorIndexSink
    vector_reranker: VectorReranker
    symbol_service: SymbolResolveService
    orchestrator: SearchOrchestrator


def build_repository_bundle(db_path: Path) -> RepositoryBundle:
    """DB 초기화/마이그레이션 후 repository bundle을 생성한다."""
    init_schema(db_path)
    ensure_migrated(db_path)
    return RepositoryBundle(
        workspace_repo=WorkspaceRepository(db_path),
        runtime_repo=RuntimeRepository(db_path),
        daemon_registry_repo=DaemonRegistryRepository(db_path),
        symbol_cache_repo=SymbolCacheRepository(db_path),
        symbol_importance_repo=SymbolImportanceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        tool_layer_repo=ToolDataLayerRepository(db_path),
        knowledge_repo=KnowledgeRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        error_event_repo=PipelineErrorEventRepository(db_path),
        perf_repo=PipelinePerfRepository(db_path),
        stage_baseline_repo=PipelineStageBaselineRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        language_probe_repo=LanguageProbeRepository(db_path),
        lsp_matrix_repo=PipelineLspMatrixRepository(db_path),
        repo_registry_repo=RepoRegistryRepository(db_path),
        vector_repo=VectorEmbeddingRepository(db_path),
        candidate_change_repo=CandidateIndexChangeRepository(db_path),
    )


def build_lsp_hub(config: LspHubRuntimeConfigDTO, hub_cls: type[Any] | None = None) -> LspHub:
    """AppConfig 기반 기본 LspHub를 생성한다."""
    from sari.lsp.hub import LspHub

    resolved_hub_cls = LspHub if hub_cls is None else hub_cls
    return resolved_hub_cls(
        request_timeout_sec=config.request_timeout_sec,
        max_instances_per_repo_language=config.max_instances_per_repo_language,
        bulk_mode_enabled=config.bulk_mode_enabled,
        bulk_max_instances_per_repo_language=config.bulk_max_instances_per_repo_language,
        interactive_reserved_slots_per_repo_language=config.interactive_reserved_slots_per_repo_language,
        interactive_timeout_sec=config.interactive_timeout_sec,
        lsp_global_soft_limit=config.lsp_global_soft_limit,
        scale_out_hot_hits=config.scale_out_hot_hits,
        file_buffer_idle_ttl_sec=config.file_buffer_idle_ttl_sec,
        file_buffer_max_open=config.file_buffer_max_open,
        java_min_major=config.java_min_major,
        max_concurrent_starts=config.max_concurrent_starts,
        max_concurrent_l1_probes=config.max_concurrent_l1_probes,
    )


def build_search_stack(
    *,
    search_config: SearchRuntimeConfigDTO,
    repos: RepositoryBundle,
    lsp_hub: LspHub,
    candidate_backend: str | None = None,
    candidate_fallback_scan: bool | None = None,
    candidate_allowed_suffixes: tuple[str, ...] | None = None,
    blend_config_version: str = "v2-config",
) -> SearchStackBundle:
    """search/orchestrator 구성요소를 공통 생성한다."""
    from sari.search.candidate_search import CandidateSearchService
    from sari.search.hierarchy_scorer import HierarchyScorer
    from sari.search.importance_scorer import ImportanceScorePolicyDTO, ImportanceScorer, ImportanceWeightsDTO
    from sari.search.orchestrator import RankingBlendConfigDTO, SearchOrchestrator
    from sari.search.symbol_resolve import SymbolResolveService
    from sari.search.vector_reranker import VectorConfigDTO, VectorIndexSink, VectorReranker

    importance_scorer = ImportanceScorer(
        file_repo=repos.file_repo,
        lsp_repo=repos.lsp_repo,
        cache_repo=repos.symbol_importance_repo,
        weights=ImportanceWeightsDTO(
            kind_class=search_config.importance_kind_class,
            kind_function=search_config.importance_kind_function,
            kind_interface=search_config.importance_kind_interface,
            kind_method=search_config.importance_kind_method,
            fan_in_weight=search_config.importance_fan_in_weight,
            filename_exact_bonus=search_config.importance_filename_exact_bonus,
            core_path_bonus=search_config.importance_core_path_bonus,
            noisy_path_penalty=search_config.importance_noisy_path_penalty,
            code_ext_bonus=search_config.importance_code_ext_bonus,
            noisy_ext_penalty=search_config.importance_noisy_ext_penalty,
            recency_24h_multiplier=search_config.importance_recency_24h_multiplier,
            recency_7d_multiplier=search_config.importance_recency_7d_multiplier,
            recency_30d_multiplier=search_config.importance_recency_30d_multiplier,
        ),
        policy=ImportanceScorePolicyDTO(
            normalize_mode=search_config.importance_normalize_mode,
            max_importance_boost=search_config.importance_max_boost,
        ),
        core_path_tokens=search_config.importance_core_path_tokens,
        noisy_path_tokens=search_config.importance_noisy_path_tokens,
        code_extensions=search_config.importance_code_extensions,
        noisy_extensions=search_config.importance_noisy_extensions,
    )
    vector_config = VectorConfigDTO(
        enabled=search_config.vector_enabled,
        model_id=search_config.vector_model_id,
        dim=search_config.vector_dim,
        candidate_k=search_config.vector_candidate_k,
        rerank_k=search_config.vector_rerank_k,
        blend_weight=search_config.vector_blend_weight,
        min_similarity_threshold=search_config.vector_min_similarity_threshold,
        max_vector_boost=search_config.vector_max_boost,
        min_token_count_for_rerank=search_config.vector_min_token_count_for_rerank,
        apply_to_item_types=search_config.vector_apply_to_item_types,
    )
    vector_sink = VectorIndexSink(repository=repos.vector_repo, config=vector_config)
    vector_reranker = VectorReranker(repository=repos.vector_repo, config=vector_config)
    hierarchy_scorer = HierarchyScorer()
    candidate_service = CandidateSearchService.build_default(
        max_file_size_bytes=512 * 1024,
        index_root=repos.file_repo.db_path.parent / "candidate_index",
        backend_mode=search_config.candidate_backend if candidate_backend is None else candidate_backend,
        enable_scan_fallback=search_config.candidate_fallback_scan if candidate_fallback_scan is None else candidate_fallback_scan,
        change_repo=repos.candidate_change_repo,
        allowed_suffixes=candidate_allowed_suffixes,
    )
    symbol_service = SymbolResolveService(
        hub=lsp_hub,
        cache_repo=repos.symbol_cache_repo,
        lsp_fallback_mode=search_config.search_lsp_fallback_mode,
        include_info_default=search_config.lsp_include_info_default,
        symbol_info_budget_sec=search_config.lsp_symbol_info_budget_sec,
        lsp_pressure_guard_enabled=search_config.search_lsp_pressure_guard_enabled,
        lsp_pressure_pending_threshold=search_config.search_lsp_pressure_pending_threshold,
        lsp_pressure_timeout_threshold=search_config.search_lsp_pressure_timeout_threshold,
        lsp_pressure_rejected_threshold=search_config.search_lsp_pressure_rejected_threshold,
        lsp_recent_failure_cooldown_sec=search_config.search_lsp_recent_failure_cooldown_sec,
    )
    orchestrator = SearchOrchestrator(
        workspace_repo=repos.workspace_repo,
        candidate_service=candidate_service,
        symbol_service=symbol_service,
        importance_scorer=importance_scorer,
        hierarchy_scorer=hierarchy_scorer,
        vector_reranker=vector_reranker,
        repo_registry_repo=repos.repo_registry_repo,
        blend_config=RankingBlendConfigDTO(
            w_rrf=search_config.ranking_w_rrf,
            w_importance=search_config.ranking_w_importance,
            w_vector=search_config.ranking_w_vector,
            w_hierarchy=search_config.ranking_w_hierarchy,
            version=blend_config_version,
        ),
    )
    return SearchStackBundle(
        candidate_service=candidate_service,
        importance_scorer=importance_scorer,
        hierarchy_scorer=hierarchy_scorer,
        vector_sink=vector_sink,
        vector_reranker=vector_reranker,
        symbol_service=symbol_service,
        orchestrator=orchestrator,
    )
