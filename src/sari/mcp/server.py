from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import BinaryIO
from sari import __version__ as SARI_VERSION
from sari.core.config import AppConfig
from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.symbol_importance_repository import SymbolImportanceRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.vector_embedding_repository import VectorEmbeddingRepository
from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.migration import ensure_migrated
from sari.db.schema import init_schema
from sari.lsp.hub import LspHub
from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.daemon_forward_policy import (
    StartDaemonFn,
    default_start_daemon,
    resolve_target,
)
from sari.mcp.daemon_router import DaemonRouter, DaemonRouterConfig
from sari.mcp.tools.admin_tools import DoctorTool, RepoCandidatesTool, RescanTool
from sari.mcp.tool_visibility import filter_tools_list_response_payload, is_hidden_tool_name
from sari.mcp.tools.pipeline_admin_tools import PipelineAutoSetTool, PipelineAutoStatusTool, PipelineAutoTickTool, PipelineAlertStatusTool, PipelineDeadListTool, PipelineDeadPurgeTool, PipelineDeadRequeueTool, PipelinePolicyGetTool, PipelinePolicySetTool
from sari.mcp.tools.pipeline_benchmark_tools import PipelineBenchmarkReportTool, PipelineBenchmarkRunTool
from sari.mcp.tools.pipeline_perf_tools import PipelinePerfReportTool, PipelinePerfRunTool
from sari.mcp.tools.pipeline_lsp_matrix_tools import PipelineLspMatrixReportTool, PipelineLspMatrixRunTool
from sari.mcp.tools.pipeline_quality_tools import PipelineQualityReportTool, PipelineQualityRunTool
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.pack1_line import PackLineOptionsDTO, render_pack_v2
from sari.mcp.tools.file_collection_tools import IndexFileTool, ListFilesTool, ReadFileTool, ScanOnceTool
from sari.mcp.tools.symbol_tools import GetCallersTool, SearchSymbolTool
from sari.mcp.tools.arg_normalizer import ArgNormalizationError, normalize_tool_arguments
from sari.mcp.transport import MCP_MODE_FRAMED, McpTransport, McpTransportParseError
from sari.mcp.server_daemon_forward import forward_once
from sari.mcp.tools.search_tool import SearchTool
from sari.mcp.tools.knowledge_tools import ArchiveContextTool, GetContextTool, GetSnippetTool, KnowledgeTool, SaveSnippetTool
from sari.mcp.tools.read_tool import DryRunDiffTool, ReadTool
from sari.mcp.tools.sari_guide_tool import SariGuideTool
from sari.mcp.tools.status_tool import StatusTool
from sari.mcp.tools.symbol_graph_tools import CallGraphHealthTool, CallGraphTool, GetImplementationsTool, ListSymbolsTool, ReadSymbolTool
from sari.search.candidate_search import CandidateSearchService
from sari.search.hierarchy_scorer import HierarchyScorer
from sari.search.importance_scorer import ImportanceScorePolicyDTO, ImportanceScorer, ImportanceWeightsDTO
from sari.search.orchestrator import RankingBlendConfigDTO, SearchOrchestrator
from sari.search.symbol_resolve import SymbolResolveService
from sari.search.vector_reranker import VectorConfigDTO, VectorIndexSink, VectorReranker
from sari.services.admin_service import AdminService
from sari.services.file_collection_service import SolidLspExtractionBackend, build_default_file_collection_service
from sari.services.pipeline_benchmark_service import BenchmarkLspExtractionBackend, PipelineBenchmarkService
from sari.services.pipeline_perf_service import PipelinePerfService
from sari.services.pipeline_control_service import PipelineControlService
from sari.services.language_probe_service import LanguageProbeService
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline_quality_service import PipelineQualityService, SerenaGoldenBackend

MAX_CONSECUTIVE_INVALID_FRAMES = 3

class McpServer:
    _TOOLS_SCHEMA_VERSION = "2026-02-18.pack1.v2-line"
    _DEFAULT_PROTOCOL_VERSION = '2024-11-05'
    _SUPPORTED_PROTOCOL_VERSIONS = (
        '2025-11-25',
        '2025-06-18',
        '2025-03-26',
        '2024-11-05',
    )

    def __init__(self, db_path: Path) -> None:
        init_schema(db_path)
        ensure_migrated(db_path)
        runtime_config = AppConfig.default()
        self._runtime_config = runtime_config
        self._db_path = db_path
        self._proxy_to_daemon = runtime_config.mcp_forward_to_daemon
        self._daemon_autostart_on_failure = runtime_config.mcp_daemon_autostart
        self._daemon_forward_timeout_sec = _parse_daemon_forward_timeout(str(runtime_config.mcp_daemon_timeout_sec))
        self._daemon_start_fn: StartDaemonFn = default_start_daemon
        self._daemon_router = DaemonRouter(
            db_path=db_path,
            config=DaemonRouterConfig(
                proxy_to_daemon=self._proxy_to_daemon,
                auto_start_on_failure=self._daemon_autostart_on_failure,
                timeout_sec=self._daemon_forward_timeout_sec,
            ),
            start_daemon_fn=self._daemon_start_fn,
            resolve_target_fn=resolve_target,
            forward_once_fn=forward_once,
        )
        self._closed = False
        self._managed_lsp_hubs: list[LspHub] = []
        workspace_repo = WorkspaceRepository(db_path)
        self._workspace_repo = workspace_repo
        runtime_repo = RuntimeRepository(db_path)
        self._runtime_repo = runtime_repo
        symbol_cache_repo = SymbolCacheRepository(db_path)
        symbol_importance_repo = SymbolImportanceRepository(db_path)
        file_repo = FileCollectionRepository(db_path)
        enrich_queue_repo = FileEnrichQueueRepository(db_path)
        body_repo = FileBodyRepository(db_path)
        lsp_repo = LspToolDataRepository(db_path)
        knowledge_repo = KnowledgeRepository(db_path)
        readiness_repo = ToolReadinessRepository(db_path)
        policy_repo = PipelinePolicyRepository(db_path)
        control_state_repo = PipelineControlStateRepository(db_path)
        event_repo = PipelineJobEventRepository(db_path)
        error_event_repo = PipelineErrorEventRepository(db_path)
        benchmark_repo = PipelineBenchmarkRepository(db_path)
        perf_repo = PipelinePerfRepository(db_path)
        quality_repo = PipelineQualityRepository(db_path)
        language_probe_repo = LanguageProbeRepository(db_path)
        lsp_matrix_repo = PipelineLspMatrixRepository(db_path)
        vector_repo = VectorEmbeddingRepository(db_path)
        candidate_change_repo = CandidateIndexChangeRepository(db_path)
        repo_registry_repo = RepoRegistryRepository(db_path)
        importance_scorer = ImportanceScorer(file_repo=file_repo, lsp_repo=lsp_repo, cache_repo=symbol_importance_repo, weights=ImportanceWeightsDTO(kind_class=runtime_config.importance_kind_class, kind_function=runtime_config.importance_kind_function, kind_interface=runtime_config.importance_kind_interface, kind_method=runtime_config.importance_kind_method, fan_in_weight=runtime_config.importance_fan_in_weight, filename_exact_bonus=runtime_config.importance_filename_exact_bonus, core_path_bonus=runtime_config.importance_core_path_bonus, noisy_path_penalty=runtime_config.importance_noisy_path_penalty, code_ext_bonus=runtime_config.importance_code_ext_bonus, noisy_ext_penalty=runtime_config.importance_noisy_ext_penalty, recency_24h_multiplier=runtime_config.importance_recency_24h_multiplier, recency_7d_multiplier=runtime_config.importance_recency_7d_multiplier, recency_30d_multiplier=runtime_config.importance_recency_30d_multiplier), policy=ImportanceScorePolicyDTO(normalize_mode=runtime_config.importance_normalize_mode, max_importance_boost=runtime_config.importance_max_boost), core_path_tokens=runtime_config.importance_core_path_tokens, noisy_path_tokens=runtime_config.importance_noisy_path_tokens, code_extensions=runtime_config.importance_code_extensions, noisy_extensions=runtime_config.importance_noisy_extensions)
        vector_config = VectorConfigDTO(enabled=runtime_config.vector_enabled, model_id=runtime_config.vector_model_id, dim=runtime_config.vector_dim, candidate_k=runtime_config.vector_candidate_k, rerank_k=runtime_config.vector_rerank_k, blend_weight=runtime_config.vector_blend_weight, min_similarity_threshold=runtime_config.vector_min_similarity_threshold, max_vector_boost=runtime_config.vector_max_boost, min_token_count_for_rerank=runtime_config.vector_min_token_count_for_rerank, apply_to_item_types=runtime_config.vector_apply_to_item_types)
        vector_sink = VectorIndexSink(repository=vector_repo, config=vector_config)
        vector_reranker = VectorReranker(repository=vector_repo, config=vector_config)
        hierarchy_scorer = HierarchyScorer()
        candidate_service = CandidateSearchService.build_default(
            max_file_size_bytes=512 * 1024,
            index_root=db_path.parent / 'candidate_index',
            backend_mode='tantivy',
            enable_scan_fallback=True,
            change_repo=candidate_change_repo,
            allowed_suffixes=runtime_config.collection_include_ext,
        )
        shared_hub = LspHub(
            request_timeout_sec=runtime_config.lsp_request_timeout_sec,
            max_instances_per_repo_language=runtime_config.lsp_max_instances_per_repo_language,
            bulk_mode_enabled=runtime_config.lsp_bulk_mode_enabled,
            bulk_max_instances_per_repo_language=runtime_config.lsp_bulk_max_instances_per_repo_language,
            interactive_reserved_slots_per_repo_language=runtime_config.lsp_interactive_reserved_slots_per_repo_language,
            interactive_timeout_sec=runtime_config.lsp_interactive_timeout_sec,
            lsp_global_soft_limit=runtime_config.lsp_global_soft_limit,
            scale_out_hot_hits=runtime_config.lsp_scale_out_hot_hits,
            file_buffer_idle_ttl_sec=runtime_config.lsp_file_buffer_idle_ttl_sec,
            file_buffer_max_open=runtime_config.lsp_file_buffer_max_open,
            java_min_major=runtime_config.lsp_java_min_major,
            max_concurrent_starts=runtime_config.lsp_max_concurrent_starts,
            max_concurrent_l1_probes=runtime_config.lsp_max_concurrent_l1_probes,
        )
        self._managed_lsp_hubs.append(shared_hub)
        file_collection_service = build_default_file_collection_service(
            workspace_repo=workspace_repo,
            file_repo=file_repo,
            enrich_queue_repo=enrich_queue_repo,
            body_repo=body_repo,
            lsp_repo=lsp_repo,
            readiness_repo=readiness_repo,
            policy_repo=policy_repo,
            event_repo=event_repo,
            error_event_repo=error_event_repo,
            candidate_index_sink=candidate_service,
            vector_index_sink=vector_sink,
            include_ext=runtime_config.collection_include_ext,
            exclude_globs=runtime_config.collection_exclude_globs,
            watcher_debounce_ms=runtime_config.watcher_debounce_ms,
            run_mode='prod',
            lsp_backend=SolidLspExtractionBackend(
                shared_hub,
                probe_workers=runtime_config.lsp_probe_workers,
                l1_workers=runtime_config.lsp_probe_l1_workers,
                force_join_ms=runtime_config.lsp_probe_force_join_ms,
                warming_retry_sec=runtime_config.lsp_probe_warming_retry_sec,
                warming_threshold=runtime_config.lsp_probe_warming_threshold,
                permanent_backoff_sec=runtime_config.lsp_probe_permanent_backoff_sec,
            ),
            l3_parallel_enabled=runtime_config.l3_parallel_enabled,
            l3_executor_max_workers=runtime_config.l3_executor_max_workers,
            l3_recent_success_ttl_sec=runtime_config.l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=runtime_config.l3_backpressure_on_interactive,
            l3_backpressure_cooldown_ms=runtime_config.l3_backpressure_cooldown_ms,
            l3_supported_languages=runtime_config.l3_supported_languages,
            lsp_probe_bootstrap_file_window=runtime_config.lsp_probe_bootstrap_file_window,
            lsp_probe_bootstrap_top_k=runtime_config.lsp_probe_bootstrap_top_k,
            lsp_probe_language_priority=runtime_config.lsp_probe_language_priority,
            lsp_probe_l1_languages=runtime_config.lsp_probe_l1_languages,
            lsp_scope_planner_enabled=runtime_config.lsp_scope_planner_enabled,
            lsp_scope_planner_shadow_mode=runtime_config.lsp_scope_planner_shadow_mode,
            lsp_scope_java_markers=runtime_config.lsp_scope_java_markers,
            lsp_scope_ts_markers=runtime_config.lsp_scope_ts_markers,
            lsp_scope_vue_markers=runtime_config.lsp_scope_vue_markers,
            lsp_scope_top_level_fallback=runtime_config.lsp_scope_top_level_fallback,
        )
        benchmark_collection_service = build_default_file_collection_service(
            workspace_repo=workspace_repo,
            file_repo=file_repo,
            enrich_queue_repo=enrich_queue_repo,
            body_repo=body_repo,
            lsp_repo=lsp_repo,
            readiness_repo=readiness_repo,
            policy_repo=policy_repo,
            event_repo=event_repo,
            error_event_repo=error_event_repo,
            run_mode='prod',
            lsp_backend=BenchmarkLspExtractionBackend(),
            persist_body_for_read=False,
            l3_parallel_enabled=runtime_config.l3_parallel_enabled,
            l3_executor_max_workers=runtime_config.l3_executor_max_workers,
            l3_recent_success_ttl_sec=runtime_config.l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=runtime_config.l3_backpressure_on_interactive,
            l3_backpressure_cooldown_ms=runtime_config.l3_backpressure_cooldown_ms,
            l3_supported_languages=runtime_config.l3_supported_languages,
            lsp_probe_bootstrap_file_window=runtime_config.lsp_probe_bootstrap_file_window,
            lsp_probe_bootstrap_top_k=runtime_config.lsp_probe_bootstrap_top_k,
            lsp_probe_language_priority=runtime_config.lsp_probe_language_priority,
            lsp_probe_l1_languages=runtime_config.lsp_probe_l1_languages,
            lsp_scope_planner_enabled=runtime_config.lsp_scope_planner_enabled,
            lsp_scope_planner_shadow_mode=runtime_config.lsp_scope_planner_shadow_mode,
            lsp_scope_java_markers=runtime_config.lsp_scope_java_markers,
            lsp_scope_ts_markers=runtime_config.lsp_scope_ts_markers,
            lsp_scope_vue_markers=runtime_config.lsp_scope_vue_markers,
            lsp_scope_top_level_fallback=runtime_config.lsp_scope_top_level_fallback,
        )
        benchmark_service = PipelineBenchmarkService(file_collection_service=benchmark_collection_service, queue_repo=enrich_queue_repo, lsp_repo=lsp_repo, policy_repo=policy_repo, benchmark_repo=benchmark_repo, artifact_root=db_path.parent / 'artifacts')
        perf_service = PipelinePerfService(
            file_collection_service=file_collection_service,
            queue_repo=enrich_queue_repo,
            benchmark_service=benchmark_service,
            perf_repo=perf_repo,
            artifact_root=db_path.parent / "artifacts",
        )
        quality_service = PipelineQualityService(file_repo=file_repo, lsp_repo=lsp_repo, quality_repo=quality_repo, golden_backend=SerenaGoldenBackend(hub=shared_hub), artifact_root=db_path.parent / 'artifacts')
        language_probe_service = LanguageProbeService(
            workspace_repo=workspace_repo,
            lsp_hub=shared_hub,
            probe_repo=language_probe_repo,
            per_language_timeout_sec=runtime_config.lsp_probe_timeout_default_sec,
            per_language_timeout_overrides={"go": runtime_config.lsp_probe_timeout_go_sec},
            lsp_request_timeout_sec=runtime_config.lsp_request_timeout_sec,
            go_warmup_timeout_sec=runtime_config.lsp_probe_timeout_go_sec,
        )
        pipeline_lsp_matrix_service = PipelineLspMatrixService(probe_service=language_probe_service, run_repo=lsp_matrix_repo)
        pipeline_control_service = PipelineControlService(policy_repo=policy_repo, event_repo=event_repo, queue_repo=enrich_queue_repo, control_state_repo=control_state_repo)
        admin_service = AdminService(
            config=AppConfig(db_path=db_path, host='127.0.0.1', preferred_port=47777, max_port_scan=50, stop_grace_sec=10),
            workspace_repo=workspace_repo,
            runtime_repo=runtime_repo,
            symbol_cache_repo=symbol_cache_repo,
            queue_repo=enrich_queue_repo,
            lsp_reconciler=shared_hub.reconcile_runtime,
        )
        orchestrator = SearchOrchestrator(workspace_repo=workspace_repo, candidate_service=candidate_service, symbol_service=SymbolResolveService(hub=shared_hub, cache_repo=symbol_cache_repo, lsp_fallback_mode=runtime_config.search_lsp_fallback_mode), importance_scorer=importance_scorer, hierarchy_scorer=hierarchy_scorer, vector_reranker=vector_reranker, blend_config=RankingBlendConfigDTO(w_rrf=runtime_config.ranking_w_rrf, w_importance=runtime_config.ranking_w_importance, w_vector=runtime_config.ranking_w_vector, w_hierarchy=runtime_config.ranking_w_hierarchy, version='v2-config'), repo_registry_repo=repo_registry_repo)
        self._file_collection_service = file_collection_service
        self._benchmark_collection_service = benchmark_collection_service
        self._search_tool = SearchTool(
            orchestrator=orchestrator,
            workspace_repo=workspace_repo,
            metrics_provider=file_collection_service.get_pipeline_metrics,
            repo_registry_repo=repo_registry_repo,
            stabilization_enabled=runtime_config.stabilization_enabled,
        )
        self._doctor_tool = DoctorTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._rescan_tool = RescanTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._repo_candidates_tool = RepoCandidatesTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._scan_once_tool = ScanOnceTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._list_files_tool = ListFilesTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._read_file_tool = ReadFileTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._index_file_tool = IndexFileTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._search_symbol_tool = SearchSymbolTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._get_callers_tool = GetCallersTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._sari_guide_tool = SariGuideTool()
        self._status_tool = StatusTool(
            workspace_repo=workspace_repo,
            runtime_repo=runtime_repo,
            file_repo=file_repo,
            lsp_repo=lsp_repo,
            language_probe_repo=language_probe_repo,
            lsp_metrics_provider=shared_hub.get_metrics,
            reconcile_state_provider=admin_service.get_runtime_reconcile_state,
        )
        self._read_tool = ReadTool(
            workspace_repo=workspace_repo,
            file_collection_service=file_collection_service,
            lsp_repo=lsp_repo,
            knowledge_repo=knowledge_repo,
            stabilization_enabled=runtime_config.stabilization_enabled,
        )
        self._dry_run_diff_tool = DryRunDiffTool(read_tool=self._read_tool)
        self._list_symbols_tool = ListSymbolsTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._read_symbol_tool = ReadSymbolTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._get_implementations_tool = GetImplementationsTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._call_graph_tool = CallGraphTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._call_graph_health_tool = CallGraphHealthTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._knowledge_tool = KnowledgeTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._save_snippet_tool = SaveSnippetTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._get_snippet_tool = GetSnippetTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._archive_context_tool = ArchiveContextTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._get_context_tool = GetContextTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._pipeline_policy_get_tool = PipelinePolicyGetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_policy_set_tool = PipelinePolicySetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_alert_status_tool = PipelineAlertStatusTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_list_tool = PipelineDeadListTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_requeue_tool = PipelineDeadRequeueTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_purge_tool = PipelineDeadPurgeTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_status_tool = PipelineAutoStatusTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_set_tool = PipelineAutoSetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_tick_tool = PipelineAutoTickTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_benchmark_run_tool = PipelineBenchmarkRunTool(workspace_repo=workspace_repo, benchmark_service=benchmark_service)
        self._pipeline_benchmark_report_tool = PipelineBenchmarkReportTool(workspace_repo=workspace_repo, benchmark_service=benchmark_service)
        self._pipeline_perf_run_tool = PipelinePerfRunTool(workspace_repo=workspace_repo, perf_service=perf_service)
        self._pipeline_perf_report_tool = PipelinePerfReportTool(workspace_repo=workspace_repo, perf_service=perf_service)
        self._pipeline_quality_run_tool = PipelineQualityRunTool(workspace_repo=workspace_repo, quality_service=quality_service)
        self._pipeline_quality_report_tool = PipelineQualityReportTool(workspace_repo=workspace_repo, quality_service=quality_service)
        self._pipeline_lsp_matrix_run_tool = PipelineLspMatrixRunTool(workspace_repo=workspace_repo, matrix_service=pipeline_lsp_matrix_service)
        self._pipeline_lsp_matrix_report_tool = PipelineLspMatrixReportTool(workspace_repo=workspace_repo, matrix_service=pipeline_lsp_matrix_service)
        self._tool_handler_attrs: dict[str, str] = {
            "search": "_search_tool",
            "sari_guide": "_sari_guide_tool",
            "status": "_status_tool",
            "doctor": "_doctor_tool",
            "rescan": "_rescan_tool",
            "repo_candidates": "_repo_candidates_tool",
            "read": "_read_tool",
            "dry_run_diff": "_dry_run_diff_tool",
            "scan_once": "_scan_once_tool",
            "list_files": "_list_files_tool",
            "read_file": "_read_file_tool",
            "index_file": "_index_file_tool",
            "list_symbols": "_list_symbols_tool",
            "read_symbol": "_read_symbol_tool",
            "search_symbol": "_search_symbol_tool",
            "get_callers": "_get_callers_tool",
            "get_implementations": "_get_implementations_tool",
            "call_graph": "_call_graph_tool",
            "call_graph_health": "_call_graph_health_tool",
            "knowledge": "_knowledge_tool",
            "save_snippet": "_save_snippet_tool",
            "get_snippet": "_get_snippet_tool",
            "archive_context": "_archive_context_tool",
            "get_context": "_get_context_tool",
            "pipeline_policy_get": "_pipeline_policy_get_tool",
            "pipeline_policy_set": "_pipeline_policy_set_tool",
            "pipeline_alert_status": "_pipeline_alert_status_tool",
            "pipeline_dead_list": "_pipeline_dead_list_tool",
            "pipeline_dead_requeue": "_pipeline_dead_requeue_tool",
            "pipeline_dead_purge": "_pipeline_dead_purge_tool",
            "pipeline_auto_status": "_pipeline_auto_status_tool",
            "pipeline_auto_set": "_pipeline_auto_set_tool",
            "pipeline_auto_tick": "_pipeline_auto_tick_tool",
            "pipeline_benchmark_run": "_pipeline_benchmark_run_tool",
            "pipeline_benchmark_report": "_pipeline_benchmark_report_tool",
            "pipeline_perf_run": "_pipeline_perf_run_tool",
            "pipeline_perf_report": "_pipeline_perf_report_tool",
            "pipeline_quality_run": "_pipeline_quality_run_tool",
            "pipeline_quality_report": "_pipeline_quality_report_tool",
            "pipeline_lsp_matrix_run": "_pipeline_lsp_matrix_run_tool",
            "pipeline_lsp_matrix_report": "_pipeline_lsp_matrix_report_tool",
        }

    def close(self) -> None:
        """MCP 서버가 생성한 런타임 리소스를 명시적으로 종료한다."""
        if self._closed:
            return
        self._closed = True
        stop_errors: list[str] = []
        for service in (self._file_collection_service, self._benchmark_collection_service):
            try:
                service.stop_background()
            except (RuntimeError, OSError, ValueError) as exc:
                stop_errors.append(str(exc))
        for hub in self._managed_lsp_hubs:
            try:
                hub.stop_all()
            except DaemonError as exc:
                stop_errors.append(exc.context.code)
        if len(stop_errors) > 0:
            raise DaemonError(
                ErrorContext(
                    code="ERR_MCP_CLOSE_FAILED",
                    message=f"MCP close 중 오류 {len(stop_errors)}건: {stop_errors[0]}",
                )
            )

    def handle_request(self, payload: dict[str, object]) -> McpResponse:
        request_id = payload.get('id')
        method = payload.get('method')
        if not isinstance(method, str):
            return McpResponse(request_id=request_id, result=None, error=McpError(code=-32600, message='invalid request'))
        if self._should_forward(payload, method):
            return self._forward_to_daemon(payload=payload, request_id=request_id)
        if method == 'initialize':
            params = payload.get('params')
            if not isinstance(params, dict):
                params = {}
            try:
                protocol_version = self._negotiate_protocol_version(params=params)
            except ValueError as exc:
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message=str(exc)))
            return McpResponse(
                request_id=request_id,
                result={
                    'protocolVersion': protocol_version,
                    'serverInfo': {'name': 'sari-v2', 'version': SARI_VERSION},
                    'schemaVersion': self._TOOLS_SCHEMA_VERSION,
                    'schema_version': self._TOOLS_SCHEMA_VERSION,
                    'capabilities': {'tools': {}},
                },
                error=None,
            )
        if method == 'sari/identify':
            return McpResponse(
                request_id=request_id,
                result={
                    'name': 'sari-v2',
                    'version': SARI_VERSION,
                    'schemaVersion': self._TOOLS_SCHEMA_VERSION,
                    'schema_version': self._TOOLS_SCHEMA_VERSION,
                    'workspaceRoot': self._default_workspace_root(),
                    'pid': os.getpid(),
                },
                error=None,
            )
        if method == 'prompts/list':
            return McpResponse(request_id=request_id, result={'prompts': []}, error=None)
        if method == 'resources/list':
            return McpResponse(request_id=request_id, result={'resources': []}, error=None)
        if method == 'resources/templates/list':
            return McpResponse(request_id=request_id, result={'resourceTemplates': []}, error=None)
        if method == 'roots/list':
            return McpResponse(request_id=request_id, result={'roots': self._roots_list()}, error=None)
        if method == 'initialized':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'notifications/initialized':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'ping':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'tools/list':
            result_payload = {'schemaVersion': self._TOOLS_SCHEMA_VERSION, 'schema_version': self._TOOLS_SCHEMA_VERSION, 'tools': [{'name': 'sari_guide', 'description': 'Return quick usage guide in pack1 format', 'inputSchema': {'type': 'object', 'properties': {}}}, {'name': 'status', 'description': 'Return repository runtime/index status in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'search', 'description': 'Search symbols/files in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'query'], 'properties': {'repo': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'doctor', 'description': 'Return runtime health checks in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'rescan', 'description': 'Invalidate symbol cache in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'repo_candidates', 'description': 'List repository candidates in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'read', 'description': 'Unified read interface in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'mode': {'type': 'string'}, 'target': {'type': 'string'}, 'path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1}, 'content': {'type': 'string'}, 'against': {'type': 'string'}, 'tag': {'type': 'string'}}}}, {'name': 'dry_run_diff', 'description': 'Legacy diff preview wrapper in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'path', 'content'], 'properties': {'repo': {'type': 'string'}, 'path': {'type': 'string'}, 'content': {'type': 'string'}, 'against': {'type': 'string'}}}}, {'name': 'scan_once', 'description': 'Scan repository files once and enqueue enrich jobs', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'list_files', 'description': 'List indexed files for repository in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}, 'prefix': {'type': 'string'}}}}, {'name': 'read_file', 'description': 'Read indexed file content in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'relative_path'], 'properties': {'repo': {'type': 'string'}, 'relative_path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'index_file', 'description': 'Incrementally index single file in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'relative_path'], 'properties': {'repo': {'type': 'string'}, 'relative_path': {'type': 'string'}}}}, {'name': 'list_symbols', 'description': 'List indexed symbols in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'read_symbol', 'description': 'Read symbol detail in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'name': {'type': 'string'}, 'symbol_id': {'type': 'string'}, 'sid': {'type': 'string'}, 'path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'search_symbol', 'description': 'Search indexed symbols in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'query'], 'properties': {'repo': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}, 'path_prefix': {'type': 'string'}}}}, {'name': 'get_callers', 'description': 'Get caller edges for symbol in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'symbol': {'type': 'string'}, 'symbol_id': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'get_implementations', 'description': 'Get implementation candidates for symbol in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'symbol': {'type': 'string'}, 'symbol_id': {'type': 'string'}, 'sid': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'call_graph', 'description': 'Get call graph for symbol in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'symbol': {'type': 'string'}, 'symbol_id': {'type': 'string'}, 'sid': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'call_graph_health', 'description': 'Get call graph health summary in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'knowledge', 'description': 'Query archived knowledge entries in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'save_snippet', 'description': 'Save code snippet to local store in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'path', 'start_line', 'end_line', 'tag'], 'properties': {'repo': {'type': 'string'}, 'path': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1}, 'end_line': {'type': 'integer', 'minimum': 1}, 'tag': {'type': 'string'}, 'note': {'type': 'string'}, 'commit': {'type': 'string'}}}}, {'name': 'get_snippet', 'description': 'Query saved snippets in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'tag': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'archive_context', 'description': 'Archive context notes in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'topic', 'content'], 'properties': {'repo': {'type': 'string'}, 'topic': {'type': 'string'}, 'content': {'type': 'string'}, 'tags': {'type': 'array', 'items': {'type': 'string'}}, 'related_files': {'type': 'array', 'items': {'type': 'string'}}}}}, {'name': 'get_context', 'description': 'Get archived context notes in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'pipeline_policy_get', 'description': 'Get pipeline policy in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_policy_set', 'description': 'Set pipeline policy in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'deletion_hold': {'type': ['string', 'boolean']}, 'l3_p95_threshold_ms': {'type': 'integer', 'minimum': 1}, 'dead_ratio_threshold_bps': {'type': 'integer', 'minimum': 1}, 'workers': {'type': 'integer', 'minimum': 1}, 'watcher_queue_max': {'type': 'integer', 'minimum': 100}, 'watcher_overflow_rescan_cooldown_sec': {'type': 'integer', 'minimum': 1}, 'alert_window_sec': {'type': 'integer', 'minimum': 60}}}}, {'name': 'pipeline_alert_status', 'description': 'Get pipeline alert snapshot in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_dead_list', 'description': 'List dead enrich jobs in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}}}}, {'name': 'pipeline_dead_requeue', 'description': 'Requeue dead enrich jobs in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}, 'all': {'type': ['boolean', 'string']}}}}, {'name': 'pipeline_dead_purge', 'description': 'Purge dead enrich jobs in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}, 'all': {'type': ['boolean', 'string']}}}}, {'name': 'pipeline_auto_status', 'description': 'Get pipeline auto-control state in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_auto_set', 'description': 'Set pipeline auto-control enabled flag in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo', 'enabled'], 'properties': {'repo': {'type': 'string'}, 'enabled': {'type': ['string', 'boolean']}}}}, {'name': 'pipeline_auto_tick', 'description': 'Evaluate pipeline auto-control once in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_benchmark_run', 'description': 'Run pipeline benchmark and return summary in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'target_files': {'type': 'integer', 'minimum': 1}, 'profile': {'type': 'string'}, 'language_filter': {'type': ['string', 'array'], 'items': {'type': 'string'}}, 'per_language_report': {'type': ['boolean', 'string']}}}}, {'name': 'pipeline_benchmark_report', 'description': 'Return latest pipeline benchmark summary in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_quality_run', 'description': 'Run L3 quality evaluation in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'limit_files': {'type': 'integer', 'minimum': 1}, 'profile': {'type': 'string'}, 'language_filter': {'type': ['string', 'array'], 'items': {'type': 'string'}}}}}, {'name': 'pipeline_quality_report', 'description': 'Return latest L3 quality summary in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}, {'name': 'pipeline_lsp_matrix_run', 'description': 'Run LSP readiness matrix and hard gate in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}, 'required_languages': {'type': ['string', 'array'], 'items': {'type': 'string'}}, 'fail_on_unavailable': {'type': ['boolean', 'string']}, 'strict_all_languages': {'type': ['boolean', 'string']}, 'strict_symbol_gate': {'type': ['boolean', 'string']}}}}, {'name': 'pipeline_lsp_matrix_report', 'description': 'Return latest LSP readiness matrix report in pack1 format', 'inputSchema': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}}}]}
            response_payload = {"result": result_payload}
            decorated_payload = filter_tools_list_response_payload(response_payload)
            decorated_result = decorated_payload.get("result")
            if isinstance(decorated_result, dict):
                result_payload = decorated_result
            return McpResponse(request_id=request_id, result=result_payload, error=None)
        if method == 'tools/call':
            params = payload.get('params')
            if not isinstance(params, dict):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message='invalid params'))
            tool_name = params.get('name')
            if is_hidden_tool_name(tool_name):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='tool not found'))
            arguments = params.get('arguments', {})
            if not isinstance(arguments, dict):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message='invalid arguments'))
            try:
                normalized = normalize_tool_arguments(str(tool_name), arguments)
                arguments = normalized.arguments
                handler_attr = self._tool_handler_attrs.get(str(tool_name))
                handler = getattr(self, handler_attr) if isinstance(handler_attr, str) and hasattr(self, handler_attr) else None
                if handler is not None and hasattr(handler, "call"):
                    raw_result = handler.call(arguments)
                    if isinstance(raw_result, dict):
                        pack_result = self._render_pack_v2(
                            tool_name=str(tool_name),
                            arguments=arguments,
                            payload=raw_result,
                        )
                        return McpResponse(request_id=request_id, result=pack_result, error=None)
                    return McpResponse(request_id=request_id, result=raw_result, error=None)
            except ValidationError as exc:
                payload = pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
                pack_result = self._render_pack_v2(
                    tool_name=str(tool_name) if isinstance(tool_name, str) else "unknown",
                    arguments=arguments,
                    payload=payload,
                )
                return McpResponse(request_id=request_id, result=pack_result, error=None)
            except ArgNormalizationError as exc:
                payload = pack1_error(
                    error=exc.to_error_dto(),
                    expected=exc.hint.expected,
                    received=exc.hint.received,
                    example=exc.hint.example,
                    normalized_from=exc.hint.normalized_from,
                )
                pack_result = self._render_pack_v2(
                    tool_name=str(tool_name) if isinstance(tool_name, str) else "unknown",
                    arguments=arguments,
                    payload=payload,
                )
                return McpResponse(request_id=request_id, result=pack_result, error=None)
            return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='tool not found'))
        return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='method not found'))

    def _should_forward(self, payload: dict[str, object], method: str) -> bool:
        _ = payload
        return self._daemon_router.should_forward(method=method)

    def _forward_to_daemon(self, payload: dict[str, object], request_id: object) -> McpResponse:
        self._daemon_router = DaemonRouter(
            db_path=self._db_path,
            config=DaemonRouterConfig(
                proxy_to_daemon=self._proxy_to_daemon,
                auto_start_on_failure=self._daemon_autostart_on_failure,
                timeout_sec=self._daemon_forward_timeout_sec,
            ),
            start_daemon_fn=self._daemon_start_fn,
            resolve_target_fn=resolve_target,
            forward_once_fn=forward_once,
        )
        response = self._daemon_router.forward(payload=payload, request_id=request_id)
        if response.error is not None:
            return response
        result_payload = response.result
        if str(payload.get("method", "")).strip() == "tools/list" and isinstance(result_payload, dict):
            if "schemaVersion" not in result_payload:
                result_payload["schemaVersion"] = self._TOOLS_SCHEMA_VERSION
            if "schema_version" not in result_payload:
                result_payload["schema_version"] = self._TOOLS_SCHEMA_VERSION
            decorated_payload = filter_tools_list_response_payload({"result": result_payload})
            decorated_result = decorated_payload.get("result")
            if isinstance(decorated_result, dict):
                result_payload = decorated_result
        return McpResponse(request_id=response.request_id, result=result_payload, error=None)

    def _roots_list(self) -> list[dict[str, str]]:
        roots: list[dict[str, str]] = []
        for workspace in self._workspace_repo.list_all():
            root_path = workspace.path
            name = Path(root_path).name if Path(root_path).name != '' else root_path
            roots.append({'uri': f'file://{root_path}', 'name': name})
        return roots

    def _default_workspace_root(self) -> str:
        workspaces = self._workspace_repo.list_all()
        if len(workspaces) == 0:
            return ''
        return workspaces[0].path

    def _negotiate_protocol_version(self, params: dict[str, object]) -> str:
        versions = self._iter_client_protocol_versions(params=params)
        for candidate in versions:
            if candidate in self._SUPPORTED_PROTOCOL_VERSIONS:
                return candidate
        strict = self._runtime_config.strict_protocol
        if strict and len(versions) > 0:
            raise ValueError('Unsupported protocol version')
        return self._DEFAULT_PROTOCOL_VERSION

    def _iter_client_protocol_versions(self, params: dict[str, object]) -> list[str]:
        versions: list[str] = []
        seen: set[str] = set()

        def _append(raw_value: object) -> None:
            if not isinstance(raw_value, str):
                return
            normalized = raw_value.strip()
            if normalized == '' or normalized in seen:
                return
            seen.add(normalized)
            versions.append(normalized)

        _append(params.get('protocolVersion'))
        supported = params.get('supportedProtocolVersions')
        if isinstance(supported, list):
            for item in supported:
                _append(item)
        capabilities = params.get('capabilities')
        if isinstance(capabilities, dict):
            cap_versions = capabilities.get('protocolVersions')
            if isinstance(cap_versions, list):
                for item in cap_versions:
                    _append(item)
        return versions

    def _render_pack_v2(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]:
        """도구 payload를 PACK1 v2 라인 포맷으로 렌더링한다."""
        include_structured = _is_structured_requested(arguments=arguments)
        return render_pack_v2(
            tool_name=tool_name,
            arguments=arguments,
            payload=payload,
            options=PackLineOptionsDTO(
                include_structured=include_structured,
                include_score=_is_score_requested(arguments=arguments),
            ),
        )


def _is_structured_requested(arguments: dict[str, object]) -> bool:
    """options.structured=1 요청 여부를 판정한다."""
    options = arguments.get("options")
    if isinstance(options, dict):
        raw_structured = options.get("structured")
        if isinstance(raw_structured, bool):
            return raw_structured
        if isinstance(raw_structured, int):
            return raw_structured == 1
        if isinstance(raw_structured, str):
            return raw_structured.strip().lower() in {"1", "true", "yes", "on"}
    raw_structured = arguments.get("structured")
    if isinstance(raw_structured, bool):
        return raw_structured
    if isinstance(raw_structured, int):
        return raw_structured == 1
    if isinstance(raw_structured, str):
        return raw_structured.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _is_score_requested(arguments: dict[str, object]) -> bool:
    """options.include_score=1 요청 여부를 판정한다."""
    options = arguments.get("options")
    if isinstance(options, dict):
        raw_score = options.get("include_score")
        if isinstance(raw_score, bool):
            return raw_score
        if isinstance(raw_score, int):
            return raw_score == 1
        if isinstance(raw_score, str):
            return raw_score.strip().lower() in {"1", "true", "yes", "on"}
    raw_score = arguments.get("include_score")
    if isinstance(raw_score, bool):
        return raw_score
    if isinstance(raw_score, int):
        return raw_score == 1
    if isinstance(raw_score, str):
        return raw_score.strip().lower() in {"1", "true", "yes", "on"}
    return False

def run_stdio_streams(db_path: Path, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    server = McpServer(db_path=db_path)
    runtime = server._runtime_repo.get_runtime()
    if runtime is not None:
        server._runtime_repo.increment_session()
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream, allow_jsonl=True)
    transport.default_mode = MCP_MODE_FRAMED
    consecutive_invalid_frames = 0
    try:
        while True:
            try:
                read_result = transport.read_message()
            except McpTransportParseError as exc:
                consecutive_invalid_frames += 1
                parse_response = McpResponse(request_id=None, result=None, error=McpError(code=-32700, message=str(exc)))
                transport.write_message(parse_response.to_dict(), mode=exc.mode)
                if (not exc.recoverable) or consecutive_invalid_frames > MAX_CONSECUTIVE_INVALID_FRAMES:
                    return 0
                if not transport.drain_for_resync(mode=exc.mode):
                    return 0
                continue
            if read_result is None:
                return 0
            consecutive_invalid_frames = 0
            payload, mode = read_result
            response = server.handle_request(payload)
            response_payload = response.to_dict()
            if str(payload.get("method", "")).strip() == "tools/list":
                response_payload = filter_tools_list_response_payload(response_payload)
            transport.write_message(response_payload, mode=mode)
    finally:
        runtime = server._runtime_repo.get_runtime()
        if runtime is not None:
            server._runtime_repo.decrement_session()
        server.close()
    return 0

def run_stdio(db_path: Path) -> int:
    input_stream = getattr(sys.stdin, 'buffer', sys.stdin)
    output_stream = getattr(sys.stdout, 'buffer', sys.stdout)
    return run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

def _parse_daemon_forward_timeout(raw_value: str) -> float:
    if raw_value == '':
        return 2.0
    try:
        parsed = float(raw_value)
    except ValueError:
        return 2.0
    if parsed <= 0:
        return 2.0
    return parsed
