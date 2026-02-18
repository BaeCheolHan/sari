"""백그라운드 데몬 프로세스를 실행한다."""

import argparse
import logging
import os
import signal
import sqlite3
import threading

import uvicorn

from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.symbol_importance_repository import SymbolImportanceRepository
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.vector_embedding_repository import VectorEmbeddingRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.migration import ensure_migrated
from sari.db.schema import init_schema
from sari.http.app import HttpContext, create_app
from sari.lsp.hub import LspHub
from sari.search.candidate_search import CandidateSearchService
from sari.search.hierarchy_scorer import HierarchyScorer
from sari.search.importance_scorer import ImportanceScorePolicyDTO, ImportanceScorer, ImportanceWeightsDTO
from sari.search.orchestrator import RankingBlendConfigDTO, SearchOrchestrator
from sari.search.symbol_resolve import SymbolResolveService
from sari.search.vector_reranker import VectorConfigDTO, VectorIndexSink, VectorReranker
from sari.core.config import AppConfig
from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import now_iso8601_utc
from sari.services.admin_service import AdminService
from sari.services.file_collection_service import SolidLspExtractionBackend, build_default_file_collection_service
from sari.services.pipeline_control_service import PipelineControlService
from sari.services.pipeline_benchmark_service import PipelineBenchmarkService
from sari.services.language_probe_service import LanguageProbeService
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline_quality_service import PipelineQualityService, SerenaGoldenBackend
from sari.services.read_facade_service import ReadFacadeService
from sari.mcp.server import McpServer

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """데몬 실행 인자를 파싱한다."""
    parser = argparse.ArgumentParser(prog="sari.daemon_process")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--run-mode", required=False, choices=["dev", "prod"], default="dev")
    return parser.parse_args()


def main() -> None:
    """데몬 HTTP 서버를 실행한다."""
    args = parse_args()
    from pathlib import Path

    db_path = Path(args.db_path)
    init_schema(db_path)
    ensure_migrated(db_path)
    runtime_repo = RuntimeRepository(db_path)
    daemon_registry_repo = DaemonRegistryRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
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
    quality_repo = PipelineQualityRepository(db_path)
    language_probe_repo = LanguageProbeRepository(db_path)
    lsp_matrix_repo = PipelineLspMatrixRepository(db_path)
    vector_repo = VectorEmbeddingRepository(db_path)
    candidate_change_repo = CandidateIndexChangeRepository(db_path)
    config = AppConfig(
        db_path=db_path,
        host=args.host,
        preferred_port=args.port,
        max_port_scan=50,
        stop_grace_sec=10,
        run_mode=str(args.run_mode),
    )
    this_pid = os.getpid()
    launch_parent_pid = os.getppid()

    lsp_hub = LspHub(request_timeout_sec=config.lsp_request_timeout_sec)
    importance_scorer = ImportanceScorer(
        file_repo=file_repo,
        lsp_repo=lsp_repo,
        cache_repo=symbol_importance_repo,
        weights=ImportanceWeightsDTO(
            kind_class=config.importance_kind_class,
            kind_function=config.importance_kind_function,
            kind_interface=config.importance_kind_interface,
            kind_method=config.importance_kind_method,
            fan_in_weight=config.importance_fan_in_weight,
            filename_exact_bonus=config.importance_filename_exact_bonus,
            core_path_bonus=config.importance_core_path_bonus,
            noisy_path_penalty=config.importance_noisy_path_penalty,
            code_ext_bonus=config.importance_code_ext_bonus,
            noisy_ext_penalty=config.importance_noisy_ext_penalty,
            recency_24h_multiplier=config.importance_recency_24h_multiplier,
            recency_7d_multiplier=config.importance_recency_7d_multiplier,
            recency_30d_multiplier=config.importance_recency_30d_multiplier,
        ),
        policy=ImportanceScorePolicyDTO(
            normalize_mode=config.importance_normalize_mode,
            max_importance_boost=config.importance_max_boost,
        ),
        core_path_tokens=config.importance_core_path_tokens,
        noisy_path_tokens=config.importance_noisy_path_tokens,
        code_extensions=config.importance_code_extensions,
        noisy_extensions=config.importance_noisy_extensions,
    )
    vector_config = VectorConfigDTO(
        enabled=config.vector_enabled,
        model_id=config.vector_model_id,
        dim=config.vector_dim,
        candidate_k=config.vector_candidate_k,
        rerank_k=config.vector_rerank_k,
        blend_weight=config.vector_blend_weight,
        min_similarity_threshold=config.vector_min_similarity_threshold,
        max_vector_boost=config.vector_max_boost,
        min_token_count_for_rerank=config.vector_min_token_count_for_rerank,
        apply_to_item_types=config.vector_apply_to_item_types,
    )
    vector_sink = VectorIndexSink(repository=vector_repo, config=vector_config)
    vector_reranker = VectorReranker(repository=vector_repo, config=vector_config)
    hierarchy_scorer = HierarchyScorer()
    candidate_service = CandidateSearchService.build_default(
        max_file_size_bytes=512 * 1024,
        index_root=config.db_path.parent / "candidate_index",
        backend_mode=config.candidate_backend,
        enable_scan_fallback=config.candidate_fallback_scan,
        change_repo=candidate_change_repo,
    )
    symbol_service = SymbolResolveService(hub=lsp_hub, cache_repo=symbol_cache_repo)
    search_orchestrator = SearchOrchestrator(
        workspace_repo=workspace_repo,
        candidate_service=candidate_service,
        symbol_service=symbol_service,
        importance_scorer=importance_scorer,
        hierarchy_scorer=hierarchy_scorer,
        vector_reranker=vector_reranker,
        blend_config=RankingBlendConfigDTO(
            w_rrf=config.ranking_w_rrf,
            w_importance=config.ranking_w_importance,
            w_vector=config.ranking_w_vector,
            w_hierarchy=config.ranking_w_hierarchy,
            version="v2-config",
        ),
    )
    admin_service = AdminService(
        config=config,
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=symbol_cache_repo,
    )
    detached_mode = os.getenv("SARI_DAEMON_DETACHED", "").strip().lower() in {"1", "true", "yes", "on"}

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
        retry_max_attempts=config.pipeline_retry_max,
        retry_backoff_base_sec=config.pipeline_backoff_base_sec,
        queue_poll_interval_ms=config.queue_poll_interval_ms,
        include_ext=config.collection_include_ext,
        exclude_globs=config.collection_exclude_globs,
        watcher_debounce_ms=config.watcher_debounce_ms,
        run_mode=config.run_mode,
        parent_alive_probe=(lambda: _is_parent_alive(launch_parent_pid, detached_mode=detached_mode)),
        lsp_backend=SolidLspExtractionBackend(lsp_hub),
    )
    pipeline_control_service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=enrich_queue_repo,
        control_state_repo=control_state_repo,
    )
    pipeline_quality_service = PipelineQualityService(
        file_repo=file_repo,
        lsp_repo=lsp_repo,
        quality_repo=quality_repo,
        golden_backend=SerenaGoldenBackend(hub=lsp_hub),
        artifact_root=config.db_path.parent / "artifacts",
    )
    pipeline_benchmark_service = PipelineBenchmarkService(
        file_collection_service=file_collection_service,
        queue_repo=enrich_queue_repo,
        lsp_repo=lsp_repo,
        policy_repo=policy_repo,
        benchmark_repo=benchmark_repo,
        artifact_root=config.db_path.parent / "artifacts",
    )
    language_probe_service = LanguageProbeService(
        workspace_repo=workspace_repo,
        lsp_hub=lsp_hub,
        probe_repo=language_probe_repo,
    )
    pipeline_lsp_matrix_service = PipelineLspMatrixService(
        probe_service=language_probe_service,
        run_repo=lsp_matrix_repo,
    )
    read_facade_service = ReadFacadeService(
        workspace_repo=workspace_repo,
        file_collection_service=file_collection_service,
        lsp_repo=lsp_repo,
        knowledge_repo=knowledge_repo,
    )

    os.environ["SARI_MCP_FORWARD_TO_DAEMON"] = "0"
    app = create_app(
        HttpContext(
            runtime_repo=runtime_repo,
            workspace_repo=workspace_repo,
            search_orchestrator=search_orchestrator,
            admin_service=admin_service,
            file_collection_service=file_collection_service,
            pipeline_control_service=pipeline_control_service,
            pipeline_benchmark_service=pipeline_benchmark_service,
            pipeline_quality_service=pipeline_quality_service,
            pipeline_lsp_matrix_service=pipeline_lsp_matrix_service,
            read_facade_service=read_facade_service,
            language_probe_repo=language_probe_repo,
            db_path=config.db_path,
        )
    )
    app.state.mcp_server = McpServer(db_path=db_path)
    stop_event = threading.Event()
    shutdown_reason: dict[str, str] = {"value": "NORMAL_SHUTDOWN"}

    def _handle_sigterm(signum: int, frame: object) -> None:
        """SIGTERM 수신 시 종료 사유를 기록하고 종료 루프로 진입한다."""
        del signum, frame
        if shutdown_reason["value"] == "":
            shutdown_reason["value"] = "NORMAL_SHUTDOWN"
        stop_event.set()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    def _heartbeat_loop() -> None:
        """데몬 heartbeat를 주기적으로 갱신한다."""
        while not stop_event.is_set():
            runtime_repo.touch_heartbeat(pid=this_pid, heartbeat_at=now_iso8601_utc())
            _touch_registry_seen(daemon_registry_repo, this_pid)
            stop_event.wait(timeout=float(config.daemon_heartbeat_interval_sec))

    def _auto_loop() -> None:
        """알람 기반 자동제어를 주기적으로 평가한다."""
        while not stop_event.is_set():
            try:
                if os.getenv("SARI_TEST_AUTO_LOOP_FAIL", "").strip() == "1":
                    raise RuntimeError("auto loop failpoint")
                if not _is_parent_alive(launch_parent_pid, detached_mode=detached_mode):
                    shutdown_reason["value"] = "ORPHAN_SELF_TERMINATE"
                    runtime_repo.mark_exit_reason(this_pid, "ORPHAN_SELF_TERMINATE", now_iso8601_utc())
                    stop_event.set()
                    os.kill(this_pid, signal.SIGTERM)
                    return
                pipeline_control_service.evaluate_auto_hold()
            except (ValidationError, sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
                # 자동제어 실패를 침묵 처리하지 않고 명시적으로 기록한다.
                log.exception("자동제어 평가 실패: %s", exc)
                try:
                    event_repo.record_event(
                        job_id="daemon:auto_hold",
                        status="AUTO_LOOP_ERROR",
                        latency_ms=0,
                        created_at=now_iso8601_utc(),
                    )
                except sqlite3.Error as event_exc:
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_DAEMON_EVENT_RECORD_FAILED",
                            message=f"자동제어 실패 이벤트 저장 실패: {event_exc}",
                        )
                    ) from event_exc
                if config.run_mode == "dev":
                    shutdown_reason["value"] = "AUTO_LOOP_FAILURE"
                    runtime_repo.mark_exit_reason(this_pid, "AUTO_LOOP_FAILURE", now_iso8601_utc())
                    stop_event.set()
                    os.kill(this_pid, signal.SIGTERM)
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_DAEMON_AUTO_LOOP_FAILED",
                            message=f"자동제어 평가 실패: {exc}",
                        )
                    ) from exc
            tick_wait = min(float(config.pipeline_auto_tick_interval_sec), float(config.orphan_ppid_check_interval_sec))
            stop_event.wait(timeout=tick_wait)

    file_collection_service.start_background()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    auto_thread = threading.Thread(target=_auto_loop, daemon=True)
    heartbeat_thread.start()
    auto_thread.start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="error")
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        auto_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        lsp_stop_error: DaemonError | None = None
        try:
            lsp_hub.stop_all()
        except DaemonError as exc:
            shutdown_reason["value"] = "LSP_STOP_FAILURE"
            runtime_repo.mark_exit_reason(this_pid, "LSP_STOP_FAILURE", now_iso8601_utc())
            lsp_stop_error = exc
        file_collection_service.stop_background()
        daemon_registry_repo.remove_by_pid(this_pid)
        runtime_repo.mark_exit_reason(this_pid, shutdown_reason["value"], now_iso8601_utc())
        if lsp_stop_error is not None:
            raise lsp_stop_error


def _is_parent_alive(parent_pid: int, detached_mode: bool=False) -> bool:
    """부모 프로세스 생존 여부를 확인한다."""
    if detached_mode:
        # 백그라운드 분리 실행 데몬은 부모 종료를 정상 상태로 간주한다.
        return True
    if parent_pid <= 1:
        # 부모가 init(1)으로 변경되면 고아 상태로 간주해 즉시 종료 경로를 탄다.
        return False
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _touch_registry_seen(registry_repo: DaemonRegistryRepository, pid: int) -> None:
    """현재 PID에 해당하는 registry 엔트리의 last_seen을 갱신한다."""
    for entry in registry_repo.list_all():
        if entry.pid == pid:
            registry_repo.touch(daemon_id=entry.daemon_id, seen_at=now_iso8601_utc())
            return


if __name__ == "__main__":
    main()
