from __future__ import annotations
import hashlib
import logging
import queue
import sqlite3
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Protocol
from solidlsp.ls_config import Language
from sari.core.exceptions import CollectionError, ErrorContext
from sari.core.config import DEFAULT_COLLECTION_EXCLUDE_GLOBS
from sari.core.language.registry import get_default_collection_extensions, get_enabled_language_names, resolve_language_from_path
from sari.core.text_decode import decode_bytes_with_policy
from sari.core.models import CandidateIndexChangeDTO, CollectionPolicyDTO, \
    CollectionScanRepoResultDTO, CollectionScanResultDTO, CollectedFileL1DTO, \
    FileReadResultDTO, PipelineMetricsDTO, now_iso8601_utc, FileEnrichJobDTO
from sari.db.repositories.file_body_repository import FileBodyDecodeError, FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_repo_relative_path
from sari.services.collection.enrich_engine import EnrichEngine
from sari.services.collection.error_policy import CollectionErrorPolicy
from sari.services.collection.l1.event_watcher import EventWatcher
from sari.services.collection.l1.scanner import FileScanner
from sari.services.collection.l1.watcher_hotness_tracker import WatcherHotnessTracker
from sari.services.collection.l5.lsp.scope_planner import LspScopePlanner
from sari.services.collection.l5.lsp.session_broker import LspBrokerLanguageProfile, LspSessionBroker
from sari.services.collection.l5.upgrade_watcher import L5AsyncUpgradeWatcher
from sari.services.collection.metrics_service import PipelineMetricsService
from sari.services.collection.pipeline_worker import PipelineWorker
from sari.services.collection.ports import CollectionRuntimePort
from sari.services.collection.repo_support import CollectionRepoSupport, WorkspaceFanoutResolver
from sari.services.collection.runtime_manager import RuntimeManager
log = logging.getLogger(__name__)

from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend
from sari.services.lsp_extraction_contracts import LspExtractionBackend, LspExtractionResultDTO
from sari.services.collection.l5.solid_lsp_probe_mixin import _ProbeStateRecord

class CandidateIndexSink(Protocol):

    def mark_repo_dirty(self, repo_root: str) -> None:
        ...

    def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
        ...

    def record_upsert(self, change: CandidateIndexChangeDTO) -> None:
        ...

    def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
        ...

class VectorIndexSink(Protocol):

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        ...

class FileCollectionService:
    PRIORITY_HIGH = 90
    PRIORITY_MEDIUM = 60
    PRIORITY_LOW = 30
    ENRICH_FLUSH_BATCH_SIZE = 128
    ENRICH_FLUSH_INTERVAL_SEC = 0.5
    ENRICH_FLUSH_MAX_BODY_BYTES = 16 * 1024 * 1024
    SCAN_FLUSH_BATCH_SIZE = 500
    SCAN_FLUSH_INTERVAL_SEC = 0.5
    SCAN_HASH_MAX_WORKERS = 8
    LSP_PREWARM_TOP_LANGUAGE_COUNT = 2
    LSP_PREWARM_MIN_LANGUAGE_FILES = 32
    WATCHER_QUEUE_MAX = 10_000
    WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC = 30
    WORKSPACE_SCAN_BUILD_MARKERS: tuple[str, ...] = (
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "WORKSPACE",
        "MODULE.bazel",
        "nx.json",
        "pnpm-workspace.yaml",
        "turbo.json",
        "composer.json",
    )

    def __init__(self, workspace_repo: WorkspaceRepository, file_repo: FileCollectionRepository, enrich_queue_repo: FileEnrichQueueRepository, body_repo: FileBodyRepository, lsp_repo: LspToolDataRepository, readiness_repo: ToolReadinessRepository, policy: CollectionPolicyDTO, lsp_backend: LspExtractionBackend, policy_repo: PipelinePolicyRepository | None=None, event_repo: PipelineJobEventRepository | None=None, error_event_repo: PipelineErrorEventRepository | None=None, candidate_index_sink: CandidateIndexSink | None=None, vector_index_sink: VectorIndexSink | None=None, run_mode: str='dev', parent_alive_probe: Callable[[], bool] | None=None, persist_body_for_read: bool=True, repo_registry_repo: RepoRegistryRepository | None=None, l3_parallel_enabled: bool=True, l3_executor_max_workers: int=0, l3_recent_success_ttl_sec: int=120, l3_backpressure_on_interactive: bool=True, l3_backpressure_cooldown_ms: int=300, l3_supported_languages: tuple[str, ...] | None=None, lsp_probe_bootstrap_file_window: int=256, lsp_probe_bootstrap_top_k: int=3, lsp_probe_language_priority: tuple[str, ...]=("go:1.5", "java:1.4", "kotlin:1.3"), lsp_probe_l1_languages: tuple[str, ...]=("go", "java", "kotlin"), lsp_probe_scan_prewarm_enabled: bool=True, lsp_session_broker_enabled: bool=True, lsp_hotness_event_window_sec: float=10.0, lsp_hotness_decay_window_sec: float=30.0, lsp_broker_backlog_min_share: float=0.2, lsp_broker_max_standby_sessions_per_lang: int=2, lsp_broker_max_standby_sessions_per_budget_group: int=2, lsp_broker_ts_vue_active_cap: int=2, lsp_broker_java_hot_lanes: int=1, lsp_broker_java_backlog_lanes: int=1, lsp_broker_java_sticky_ttl_sec: float=600.0, lsp_broker_java_switch_cooldown_sec: float=5.0, lsp_broker_java_min_lease_ms: int=1500, lsp_broker_ts_hot_lanes: int=1, lsp_broker_ts_backlog_lanes: int=1, lsp_broker_ts_sticky_ttl_sec: float=180.0, lsp_broker_ts_switch_cooldown_sec: float=2.0, lsp_broker_ts_min_lease_ms: int=500, lsp_broker_vue_hot_lanes: int=1, lsp_broker_vue_backlog_lanes: int=1, lsp_broker_vue_sticky_ttl_sec: float=240.0, lsp_broker_vue_switch_cooldown_sec: float=3.0, lsp_broker_vue_min_lease_ms: int=800, lsp_scope_active_languages: tuple[str, ...] | None=None, l5_call_rate_total_max: float=0.05, l5_call_rate_batch_max: float=0.01, l5_calls_per_min_per_lang_max: int=30, l5_tokens_per_10sec_global_max: int=120, l5_tokens_per_10sec_per_lang_max: int=30, l5_tokens_per_10sec_per_workspace_max: int=20, pipeline_l5_worker_count: int=2, l3_query_compile_ms_budget: float=10.0, l3_query_budget_ms: float=30.0, l3_tree_sitter_executor_mode: str="inline", l3_tree_sitter_subinterp_workers: int=4, l3_tree_sitter_subinterp_min_bytes: int=4096, l5_db_short_circuit_enabled: bool=True, tool_layer_repo: ToolDataLayerRepository | None=None, event_bus: object=None, l5_async_quality_upgrade_enabled: bool=True, l5_async_quality_upgrade_batch_size: int=50, l5_async_quality_upgrade_poll_interval_sec: float=5.0) -> None:
        self._workspace_repo = workspace_repo
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._body_repo = body_repo
        self._lsp_repo = lsp_repo
        self._readiness_repo = readiness_repo
        self._policy = policy
        self._lsp_backend = lsp_backend
        self._policy_repo = policy_repo
        self._event_repo = event_repo
        self._error_event_repo = error_event_repo
        self._candidate_index_sink = candidate_index_sink
        self._vector_index_sink = vector_index_sink
        self._repo_registry_repo = repo_registry_repo
        normalized_run_mode = str(run_mode or "").strip().lower()
        self._run_mode = "prod" if normalized_run_mode in {"prod", "production", "release"} else "dev"
        self._parent_alive_probe = parent_alive_probe
        self._persist_body_for_read = persist_body_for_read
        self._l3_parallel_enabled = bool(l3_parallel_enabled)
        self._lsp_probe_bootstrap_file_window = max(1, int(lsp_probe_bootstrap_file_window))
        self._lsp_probe_bootstrap_top_k = max(1, int(lsp_probe_bootstrap_top_k))
        self._lsp_probe_language_priority_weights = _parse_language_priority_weights(lsp_probe_language_priority)
        self._lsp_probe_l1_languages = tuple(item.strip() for item in lsp_probe_l1_languages if item.strip() != "")
        self._lsp_probe_scan_prewarm_enabled = bool(lsp_probe_scan_prewarm_enabled)
        if l3_supported_languages is None:
            self._l3_supported_languages = tuple(item.strip() for item in get_enabled_language_names() if item.strip() != "")
        else:
            self._l3_supported_languages = tuple(item.strip() for item in l3_supported_languages if item.strip() != "")
        watcher_queue_max, watcher_overflow_rescan_cooldown_sec = self._resolve_watcher_runtime_settings()
        self._watcher_queue_max = watcher_queue_max
        self._watcher_overflow_rescan_cooldown_sec = watcher_overflow_rescan_cooldown_sec
        self._stop_event = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._enrich_threads: list[threading.Thread] = []
        self._watcher_thread: threading.Thread | None = None
        self._event_queue: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=self._watcher_queue_max)
        self._l3_ready_queue: queue.Queue[FileEnrichJobDTO] = queue.Queue()
        self._watcher_debounce_ms = 300
        self._debounce_events: dict[tuple[str, str], tuple[float, str, str]] = {}
        self._debounce_lock = threading.Lock()
        self._watcher_drop_count = 0
        self._watcher_overflow_count = 0
        self._watcher_last_overflow_at: str | None = None
        self._watcher_rescan_queue: queue.Queue[str] = queue.Queue()
        self._watcher_rescan_pending_roots: set[str] = set()
        self._watcher_rescan_lock = threading.Lock()
        self._watcher_rescan_thread: threading.Thread | None = None
        self._enrich_latency_samples_ms: list[float] = []
        self._throughput_samples_jobs_per_sec: list[float] = []
        self._throughput_ema_jobs_per_sec = 0.0
        self._throughput_alpha = 0.2
        self._lsp_session_broker_enabled = bool(lsp_session_broker_enabled)
        self._event_bus = event_bus
        self._l5_async_quality_upgrade_enabled = bool(l5_async_quality_upgrade_enabled)
        self._l5_async_quality_upgrade_batch_size = max(1, int(l5_async_quality_upgrade_batch_size))
        self._l5_async_quality_upgrade_poll_interval_sec = max(0.5, float(l5_async_quality_upgrade_poll_interval_sec))
        self._watcher_hotness_tracker = WatcherHotnessTracker(
            event_window_sec=lsp_hotness_event_window_sec,
            decay_window_sec=lsp_hotness_decay_window_sec,
            now_monotonic=time.monotonic,
            scope_cache_invalidator=self._invalidate_scope_caches_from_watcher_signal,
        )
        broker_profiles = self._build_lsp_broker_profiles(
            enabled=self._lsp_session_broker_enabled,
            java_hot_lanes=lsp_broker_java_hot_lanes,
            java_backlog_lanes=lsp_broker_java_backlog_lanes,
            java_sticky_ttl_sec=lsp_broker_java_sticky_ttl_sec,
            java_switch_cooldown_sec=lsp_broker_java_switch_cooldown_sec,
            java_min_lease_ms=lsp_broker_java_min_lease_ms,
            ts_hot_lanes=lsp_broker_ts_hot_lanes,
            ts_backlog_lanes=lsp_broker_ts_backlog_lanes,
            ts_sticky_ttl_sec=lsp_broker_ts_sticky_ttl_sec,
            ts_switch_cooldown_sec=lsp_broker_ts_switch_cooldown_sec,
            ts_min_lease_ms=lsp_broker_ts_min_lease_ms,
            vue_hot_lanes=lsp_broker_vue_hot_lanes,
            vue_backlog_lanes=lsp_broker_vue_backlog_lanes,
            vue_sticky_ttl_sec=lsp_broker_vue_sticky_ttl_sec,
            vue_switch_cooldown_sec=lsp_broker_vue_switch_cooldown_sec,
            vue_min_lease_ms=lsp_broker_vue_min_lease_ms,
        )
        self._lsp_session_broker = LspSessionBroker(
            profiles=broker_profiles,
            max_standby_sessions_per_lang=max(0, int(lsp_broker_max_standby_sessions_per_lang)),
            max_standby_sessions_per_budget_group=max(0, int(lsp_broker_max_standby_sessions_per_budget_group)),
            backlog_min_share=min(1.0, max(0.0, float(lsp_broker_backlog_min_share))),
            now_monotonic=time.monotonic,
        )
        self._configure_lsp_backend_runtime(
            ts_vue_active_cap=lsp_broker_ts_vue_active_cap,
            lsp_scope_active_languages=lsp_scope_active_languages,
        )
        self._metrics_lock = threading.Lock()
        self._worker_state = 'running'
        self._indexing_mode = 'steady'
        self._repo_support = CollectionRepoSupport(
            workspace_repo=self._workspace_repo,
            policy=self._policy,
            policy_repo=self._policy_repo,
            lsp_backend=self._lsp_backend,
            repo_registry_repo=self._repo_registry_repo,
            lsp_prewarm_min_language_files=self.LSP_PREWARM_MIN_LANGUAGE_FILES,
            lsp_prewarm_top_language_count=self.LSP_PREWARM_TOP_LANGUAGE_COUNT,
            event_bus=self._event_bus,
        )
        self._fanout_resolver = WorkspaceFanoutResolver(
            workspace_repo=self._workspace_repo,
            load_gitignore_spec=self._repo_support.load_gitignore_spec,
            is_collectible=self._repo_support.is_collectible,
            build_markers=self.WORKSPACE_SCAN_BUILD_MARKERS,
        )
        self._scanner = FileScanner(
            file_repo=self._file_repo,
            enrich_queue_repo=self._enrich_queue_repo,
            candidate_index_sink=self._candidate_index_sink,
            resolve_lsp_language=self._repo_support.resolve_lsp_language,
            configure_lsp_prewarm_languages=self._repo_support.configure_lsp_prewarm_languages,
            schedule_lsp_probe_for_file=(
                self._repo_support.schedule_lsp_probe_for_file
                if self._lsp_probe_scan_prewarm_enabled
                else None
            ),
            resolve_repo_identity=self._repo_support.resolve_repo_identity,
            load_gitignore_spec=self._repo_support.load_gitignore_spec,
            is_collectible=self._repo_support.is_collectible,
            priority_low=self.PRIORITY_LOW,
            priority_medium=self.PRIORITY_MEDIUM,
            scan_flush_batch_size=self.SCAN_FLUSH_BATCH_SIZE,
            scan_flush_interval_sec=self.SCAN_FLUSH_INTERVAL_SEC,
            scan_hash_max_workers=self.SCAN_HASH_MAX_WORKERS,
            bootstrap_file_window=self._lsp_probe_bootstrap_file_window,
            bootstrap_top_k=self._lsp_probe_bootstrap_top_k,
            language_priority_weights=self._lsp_probe_language_priority_weights,
        )
        self._error_policy = CollectionErrorPolicy(
            error_event_repo=self._error_event_repo,
            run_mode=self._run_mode,
            stop_background=self._stop_event.set,
        )
        self._enrich_engine = EnrichEngine(
            file_repo=self._file_repo,
            enrich_queue_repo=self._enrich_queue_repo,
            body_repo=self._body_repo,
            lsp_repo=self._lsp_repo,
            readiness_repo=self._readiness_repo,
            policy=self._policy,
            lsp_backend=self._lsp_backend,
            policy_repo=self._policy_repo,
            event_repo=self._event_repo,
            vector_index_sink=self._vector_index_sink,
            run_mode=self._run_mode,
            persist_body_for_read=self._persist_body_for_read,
            l3_ready_queue=self._l3_ready_queue,
            error_policy=self._error_policy,
            record_enrich_latency=self._record_enrich_latency,
            assert_parent_alive=self._assert_parent_alive,
            flush_batch_size=self.ENRICH_FLUSH_BATCH_SIZE,
            flush_interval_sec=self.ENRICH_FLUSH_INTERVAL_SEC,
            flush_max_body_bytes=self.ENRICH_FLUSH_MAX_BODY_BYTES,
            l3_parallel_enabled=self._l3_parallel_enabled,
            l3_executor_max_workers=l3_executor_max_workers,
            l3_recent_success_ttl_sec=l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=l3_backpressure_on_interactive,
            l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms,
            l3_supported_languages=self._l3_supported_languages,
            lsp_probe_l1_languages=self._lsp_probe_l1_languages,
            l5_call_rate_total_max=l5_call_rate_total_max,
            l5_call_rate_batch_max=l5_call_rate_batch_max,
            l5_calls_per_min_per_lang_max=l5_calls_per_min_per_lang_max,
            l5_tokens_per_10sec_global_max=l5_tokens_per_10sec_global_max,
            l5_tokens_per_10sec_per_lang_max=l5_tokens_per_10sec_per_lang_max,
            l5_tokens_per_10sec_per_workspace_max=l5_tokens_per_10sec_per_workspace_max,
            l5_admission_shadow_enabled=(self._run_mode == "prod"),
            l5_admission_enforced=False,
            l3_query_compile_ms_budget=l3_query_compile_ms_budget,
            l3_query_budget_ms=l3_query_budget_ms,
            l3_tree_sitter_executor_mode=l3_tree_sitter_executor_mode,
            l3_tree_sitter_subinterp_workers=l3_tree_sitter_subinterp_workers,
            l3_tree_sitter_subinterp_min_bytes=l3_tree_sitter_subinterp_min_bytes,
            l5_db_short_circuit_enabled=l5_db_short_circuit_enabled,
            tool_layer_repo=tool_layer_repo,
            event_bus=self._event_bus,
        )
        self._pipeline_worker = PipelineWorker(
            process_enrich_jobs=self._enrich_engine.process_enrich_jobs,
            process_enrich_jobs_l2=self._enrich_engine.process_enrich_jobs_l2,
            process_enrich_jobs_l3=self._enrich_engine.process_enrich_jobs_l3,
        )
        self._runtime_manager = RuntimeManager(
            stop_event=self._stop_event,
            enrich_queue_repo=self._enrich_queue_repo,
            workspace_repo=self._workspace_repo,
            policy=self._policy,
            policy_repo=self._policy_repo,
            assert_parent_alive=self._assert_parent_alive,
            scan_once=self.scan_once,
            process_enrich_jobs_bootstrap=self._enrich_engine.process_enrich_jobs_bootstrap,
            process_enrich_jobs_l5=self._enrich_engine.process_enrich_jobs_l5,
            handle_background_collection_error=self._handle_background_collection_error_proxy,
            prune_error_events_if_needed=self._error_policy.prune_error_events_if_needed,
            watcher_loop=self._watcher_loop,
            l5_worker_count=max(1, int(pipeline_l5_worker_count)),
        )
        self._watcher = EventWatcher(
            workspace_repo=self._workspace_repo,
            file_repo=self._file_repo,
            candidate_index_sink=self._candidate_index_sink,
            event_queue=self._event_queue,
            stop_event=self._stop_event,
            debounce_events=self._debounce_events,
            debounce_lock=self._debounce_lock,
            watcher_debounce_ms=lambda: self._watcher_debounce_ms,
            assert_parent_alive=self._assert_parent_alive,
            index_file_with_priority=self._index_file_with_priority,
            handle_background_collection_error=self._handle_background_collection_error_proxy,
            priority_high=self.PRIORITY_HIGH,
            set_observer=self._runtime_manager.set_observer,
            watcher_overflow_rescan_cooldown_sec=self._watcher_overflow_rescan_cooldown_sec,
            now_monotonic=time.monotonic,
            on_watcher_queue_overflow=self._record_watcher_queue_overflow,
            schedule_rescan=self._schedule_rescan_from_watcher,
            on_watcher_file_race=self._record_watcher_file_race,
            on_watcher_signal=self._on_watcher_signal,
        )
        self._metrics_service = PipelineMetricsService(
            refresh_indexing_mode=self._enrich_engine.refresh_indexing_mode,
            enrich_queue_repo=self._enrich_queue_repo,
            file_repo=self._file_repo,
            l3_queue_size=lambda: self._l3_ready_queue.qsize(),
            metrics_lock=self._metrics_lock,
            enrich_latency_samples_ms=self._enrich_latency_samples_ms,
            throughput_samples_jobs_per_sec=self._throughput_samples_jobs_per_sec,
            get_throughput_ema=lambda: self._throughput_ema_jobs_per_sec,
            set_throughput_ema=self._set_throughput_ema_jobs_per_sec,
            throughput_alpha=self._throughput_alpha,
            enrich_threads_count=self._runtime_manager.enrich_thread_count,
            compute_coverage_bps=self._enrich_engine.compute_coverage_bps,
            indexing_mode=self._enrich_engine.indexing_mode,
            worker_state=lambda: self._worker_state,
            last_error_code=self._error_policy.last_error_code,
            last_error_message=self._error_policy.last_error_message,
            last_error_at=self._error_policy.last_error_at,
            watcher_queue_depth=lambda: self._event_queue.qsize(),
            watcher_drop_count=self._watcher_drop_count_snapshot,
            watcher_overflow_count=self._watcher_overflow_count_snapshot,
            watcher_last_overflow_at=self._watcher_last_overflow_at_snapshot,
            lsp_metrics_snapshot=self._lsp_runtime_metrics_snapshot,
        )
        self._l5_upgrade_watcher = L5AsyncUpgradeWatcher(
            event_bus=self._event_bus,
            enrich_queue_repo=self._enrich_queue_repo,
            tool_layer_repo=tool_layer_repo,
            workspace_id="",
            batch_size=self._l5_async_quality_upgrade_batch_size,
            poll_interval_sec=self._l5_async_quality_upgrade_poll_interval_sec,
            enabled=self._l5_async_quality_upgrade_enabled and self._event_bus is not None,
        )

    def _resolve_watcher_runtime_settings(self) -> tuple[int, int]:
        watcher_queue_max = self.WATCHER_QUEUE_MAX
        watcher_overflow_rescan_cooldown_sec = self.WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC
        if self._policy_repo is None:
            return watcher_queue_max, watcher_overflow_rescan_cooldown_sec
        try:
            runtime_policy = self._policy_repo.get_policy()
            watcher_queue_max = max(100, int(runtime_policy.watcher_queue_max))
            watcher_overflow_rescan_cooldown_sec = max(
                1, int(runtime_policy.watcher_overflow_rescan_cooldown_sec)
            )
        except (RuntimeError, ValueError):
            # 정책 조회 실패 시 기본 안전값으로 동작한다.
            watcher_queue_max = self.WATCHER_QUEUE_MAX
            watcher_overflow_rescan_cooldown_sec = self.WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC
        return watcher_queue_max, watcher_overflow_rescan_cooldown_sec

    @staticmethod
    def _build_lsp_broker_profiles(
        *,
        enabled: bool,
        java_hot_lanes: int,
        java_backlog_lanes: int,
        java_sticky_ttl_sec: float,
        java_switch_cooldown_sec: float,
        java_min_lease_ms: int,
        ts_hot_lanes: int,
        ts_backlog_lanes: int,
        ts_sticky_ttl_sec: float,
        ts_switch_cooldown_sec: float,
        ts_min_lease_ms: int,
        vue_hot_lanes: int,
        vue_backlog_lanes: int,
        vue_sticky_ttl_sec: float,
        vue_switch_cooldown_sec: float,
        vue_min_lease_ms: int,
    ) -> dict[str, LspBrokerLanguageProfile]:
        if not enabled:
            return {}
        return {
            "java": LspBrokerLanguageProfile(
                language="java",
                hot_lanes=max(0, int(java_hot_lanes)),
                backlog_lanes=max(0, int(java_backlog_lanes)),
                sticky_idle_ttl_sec=max(0.0, float(java_sticky_ttl_sec)),
                switch_cooldown_sec=max(0.0, float(java_switch_cooldown_sec)),
                min_lease_ms=max(0, int(java_min_lease_ms)),
            ),
            "typescript": LspBrokerLanguageProfile(
                language="typescript",
                hot_lanes=max(0, int(ts_hot_lanes)),
                backlog_lanes=max(0, int(ts_backlog_lanes)),
                sticky_idle_ttl_sec=max(0.0, float(ts_sticky_ttl_sec)),
                switch_cooldown_sec=max(0.0, float(ts_switch_cooldown_sec)),
                min_lease_ms=max(0, int(ts_min_lease_ms)),
                shared_budget_group="ts-vue",
            ),
            "vue": LspBrokerLanguageProfile(
                language="vue",
                hot_lanes=max(0, int(vue_hot_lanes)),
                backlog_lanes=max(0, int(vue_backlog_lanes)),
                sticky_idle_ttl_sec=max(0.0, float(vue_sticky_ttl_sec)),
                switch_cooldown_sec=max(0.0, float(vue_switch_cooldown_sec)),
                min_lease_ms=max(0, int(vue_min_lease_ms)),
                shared_budget_group="ts-vue",
            ),
        }

    def _configure_lsp_backend_runtime(
        self,
        *,
        ts_vue_active_cap: int,
        lsp_scope_active_languages: tuple[str, ...] | None,
    ) -> None:
        if self._lsp_session_broker_enabled:
            self._lsp_session_broker.set_budget_group_active_cap("ts-vue", max(0, int(ts_vue_active_cap)))

        configure_session_runtime = getattr(self._lsp_backend, "configure_session_runtime", None)
        if callable(configure_session_runtime):
            configure_session_runtime(
                session_broker=self._lsp_session_broker,
                watcher_hotness_tracker=self._watcher_hotness_tracker,
                enabled=self._lsp_session_broker_enabled,
            )

        configure_scope_runtime_policy = getattr(self._lsp_backend, "configure_scope_runtime_policy", None)
        if callable(configure_scope_runtime_policy):
            configure_scope_runtime_policy(active_languages=lsp_scope_active_languages)

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        """L1 스캔을 실행한다. workspace 컨테이너는 top-level repo fan-out을 수행한다."""
        root_path = Path(repo_root).expanduser().resolve()
        fanout_targets = self._fanout_resolver.resolve_targets(root_path)
        if len(fanout_targets) == 0:
            self._cleanup_stale_fanout_rows_for_single_repo(root_path=root_path)
            return self._scanner_scan_once(repo_root=str(root_path), scope_repo_root=str(root_path))
        return self._scan_workspace_fanout(root_path=root_path, targets=fanout_targets)

    def _cleanup_stale_fanout_rows_for_single_repo(self, *, root_path: Path) -> None:
        """단일 repo 스캔으로 전환 시 과거 fan-out child 산출물(active)을 정리한다."""
        if not root_path.exists() or not root_path.is_dir():
            return
        root_resolved = str(root_path.resolve())
        stale_repo_roots = self._file_repo.list_active_repo_roots_in_scope_excluding(
            scope_repo_root=root_resolved,
            excluded_repo_root=root_resolved,
        )
        if len(stale_repo_roots) == 0:
            return
        now_iso = now_iso8601_utc()
        stale_deleted = self._file_repo.mark_all_active_as_deleted_in_scope_excluding(
            scope_repo_root=root_resolved,
            excluded_repo_root=root_resolved,
            updated_at=now_iso,
        )
        if stale_deleted > 0 and self._candidate_index_sink is not None:
            for child_root in stale_repo_roots:
                self._candidate_index_sink.mark_repo_dirty(child_root)

    def _scan_workspace_fanout(self, root_path: Path, targets: list[Path]) -> CollectionScanResultDTO:
        """workspace 컨테이너 하위 repo를 top-level 단위로 순차 스캔한다."""
        scanned_total = 0
        indexed_total = 0
        deleted_total = 0
        # fan-out 모드에서는 workspace-root 행이 legacy/stale 상태로 남을 수 있으므로 먼저 비활성화한다.
        legacy_deleted = self._file_repo.mark_all_active_as_deleted(
            repo_root=str(root_path),
            updated_at=now_iso8601_utc(),
        )
        deleted_total += legacy_deleted
        if legacy_deleted > 0 and self._candidate_index_sink is not None:
            self._candidate_index_sink.mark_repo_dirty(str(root_path))
        # fan-out 대상이 변경된 경우(예: build/bin 제외 정책 추가) 이전 top-level module row가
        # active 상태로 남아 stale 노출될 수 있으므로 비대상 child repo_root를 선제 삭제한다.
        target_roots = {str(target.resolve()) for target in targets}
        for child in root_path.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            child_root = str(child.resolve())
            if child_root in target_roots:
                continue
            stale_deleted = self._file_repo.mark_all_active_as_deleted_in_scope(
                repo_root=child_root,
                scope_repo_root=str(root_path),
                updated_at=now_iso8601_utc(),
            )
            deleted_total += stale_deleted
            if stale_deleted > 0 and self._candidate_index_sink is not None:
                self._candidate_index_sink.mark_repo_dirty(child_root)
        succeeded = 0
        failed = 0
        results: list[CollectionScanRepoResultDTO] = []
        for target in targets:
            try:
                scan_result = self._scanner_scan_once(repo_root=str(target), scope_repo_root=str(root_path))
                scanned_total += scan_result.scanned_count
                indexed_total += scan_result.indexed_count
                deleted_total += scan_result.deleted_count
                succeeded += 1
                results.append(
                    CollectionScanRepoResultDTO(
                        repo_root=str(target),
                        scanned_count=scan_result.scanned_count,
                        indexed_count=scan_result.indexed_count,
                        deleted_count=scan_result.deleted_count,
                    )
                )
            except CollectionError as exc:
                failed += 1
                results.append(
                    CollectionScanRepoResultDTO(
                        repo_root=str(target),
                        scanned_count=0,
                        indexed_count=0,
                        deleted_count=0,
                        status="error",
                        error_code=exc.context.code,
                        error_message=exc.context.message,
                    )
                )
        return CollectionScanResultDTO(
            scanned_count=scanned_total,
            indexed_count=indexed_total,
            deleted_count=deleted_total,
            mode="fanout_top_level",
            target_repo_count=len(targets),
            succeeded_repo_count=succeeded,
            failed_repo_count=failed,
            repo_results=tuple(results),
        )

    def index_file(self, repo_root: str, relative_path: str) -> CollectionScanResultDTO:
        """단일 파일 인덱싱을 전용 스캐너 컴포넌트로 위임한다."""
        return self._scanner_index_file(repo_root=repo_root, relative_path=relative_path, scope_repo_root=repo_root)

    def _scanner_scan_once(self, *, repo_root: str, scope_repo_root: str) -> CollectionScanResultDTO:
        """신/구 시그니처 모두 호환해 scanner.scan_once를 호출한다."""
        scanner_scan_once = getattr(self._scanner, "scan_once")
        try:
            return scanner_scan_once(repo_root, scope_repo_root=scope_repo_root)
        except TypeError:
            return scanner_scan_once(repo_root)

    def _scanner_index_file(self, *, repo_root: str, relative_path: str, scope_repo_root: str) -> CollectionScanResultDTO:
        """신/구 시그니처 모두 호환해 scanner.index_file을 호출한다."""
        scanner_index_file = getattr(self._scanner, "index_file")
        try:
            return scanner_index_file(repo_root, relative_path, scope_repo_root=scope_repo_root)
        except TypeError:
            return scanner_index_file(repo_root, relative_path)

    def process_enrich_jobs(self, limit: int) -> int:
        """L2/L3 통합 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs(limit)

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs_l2(limit)

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs_l3(limit)

    def process_enrich_jobs_l5(self, limit: int) -> int:
        """L5 보강 처리를 전용 엔진 컴포넌트로 위임한다."""
        return self._enrich_engine.process_enrich_jobs_l5(limit)

    def _watcher_loop(self) -> None:
        """watcher 루프를 전용 이벤트 컴포넌트로 위임한다."""
        try:
            self._watcher.watcher_loop()
        except CollectionError as exc:
            if self._handle_background_collection_error_proxy(exc=exc, phase="watcher_loop", worker_name="watcher"):
                return
        except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
            wrapped = CollectionError(
                ErrorContext(
                    code="ERR_WATCHER_RUNTIME_FAILED",
                    message=f"watcher 루프 실패: {exc}",
                )
            )
            if self._handle_background_collection_error_proxy(exc=wrapped, phase="watcher_loop", worker_name="watcher"):
                return

    def _handle_fs_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """파일 시스템 이벤트 처리를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.handle_fs_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def _push_debounced_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """디바운스 이벤트 적재를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.push_debounced_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def _flush_debounced_events(self) -> None:
        """디바운스 이벤트 flush를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.flush_debounced_events()

    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        """파이프라인 메트릭 계산을 전용 메트릭 컴포넌트로 위임한다."""
        return self._metrics_service.get_pipeline_metrics()

    def _record_enrich_latency(self, latency_ms: float) -> None:
        """처리 지연시간 기록을 전용 메트릭 컴포넌트로 위임한다."""
        self._metrics_service.record_enrich_latency(latency_ms)

    def _set_throughput_ema_jobs_per_sec(self, value: float) -> None:
        """처리량 EMA 값을 명시적으로 갱신한다."""
        self._throughput_ema_jobs_per_sec = value

    def _watcher_drop_count_snapshot(self) -> int:
        """watcher drop 카운트 스냅샷을 반환한다."""
        with self._metrics_lock:
            return int(self._watcher_drop_count)

    def _watcher_overflow_count_snapshot(self) -> int:
        """watcher overflow 카운트 스냅샷을 반환한다."""
        with self._metrics_lock:
            return int(self._watcher_overflow_count)

    def _watcher_last_overflow_at_snapshot(self) -> str | None:
        """watcher 마지막 overflow 시각을 반환한다."""
        with self._metrics_lock:
            return self._watcher_last_overflow_at

    def _lsp_runtime_metrics_snapshot(self) -> dict[str, float]:
        """LSP 런타임 메트릭 스냅샷을 반환한다."""
        merged: dict[str, float] = {}
        if hasattr(self._lsp_backend, "get_runtime_metrics"):
            try:
                metrics = getattr(self._lsp_backend, "get_runtime_metrics")()
                if isinstance(metrics, dict):
                    merged.update(
                        {
                            str(key): float(value)
                            for key, value in metrics.items()
                            if isinstance(value, (int, float))
                        }
                    )
            except (RuntimeError, OSError, ValueError, TypeError):
                merged = {}
        enrich_metrics_getter = getattr(self._enrich_engine, "get_runtime_metrics", None)
        if callable(enrich_metrics_getter):
            try:
                enrich_metrics = enrich_metrics_getter()
                if isinstance(enrich_metrics, dict):
                    merged.update(
                        {
                            str(key): float(value)
                            for key, value in enrich_metrics.items()
                            if isinstance(value, (int, float))
                        }
                    )
            except (RuntimeError, OSError, ValueError, TypeError):
                ...
        try:
            watcher_metrics = self._watcher_hotness_tracker.get_metrics()
            if isinstance(watcher_metrics, dict):
                merged.update(
                    {
                        str(key): float(value)
                        for key, value in watcher_metrics.items()
                        if isinstance(value, (int, float))
                    }
                )
        except (RuntimeError, OSError, ValueError, TypeError):
            ...
        try:
            broker_metrics = self._lsp_session_broker.get_metrics()
            if isinstance(broker_metrics, dict):
                merged.update(
                    {
                        str(key): float(value)
                        for key, value in broker_metrics.items()
                        if isinstance(value, (int, float))
                    }
                )
        except (RuntimeError, OSError, ValueError, TypeError):
            ...
        return merged

    def get_l5_admission_status(self) -> dict[str, object]:
        """L5 admission 모드/요약 메트릭을 반환한다."""
        enrich_engine = getattr(self, "_enrich_engine", None)
        if enrich_engine is None:
            return {}
        status: dict[str, object] = {
            "shadow_enabled": bool(getattr(enrich_engine, "_l5_admission_shadow_enabled", False)),
            "enforced": bool(getattr(enrich_engine, "_l5_admission_enforced", False)),
            "limits": {
                "call_rate_total_max": float(getattr(enrich_engine, "_l5_call_rate_total_max", 0.0)),
                "call_rate_batch_max": float(getattr(enrich_engine, "_l5_call_rate_batch_max", 0.0)),
                "calls_per_min_per_lang_max": int(getattr(enrich_engine, "_l5_calls_per_min_per_lang_max", 0)),
                "tokens_per_10sec_global_max": int(getattr(enrich_engine, "_l5_tokens_per_10sec_global_max", 0)),
                "tokens_per_10sec_per_lang_max": int(getattr(enrich_engine, "_l5_tokens_per_10sec_per_lang_max", 0)),
                "tokens_per_10sec_per_workspace_max": int(getattr(enrich_engine, "_l5_tokens_per_10sec_per_workspace_max", 0)),
            },
        }
        metrics_getter = getattr(enrich_engine, "get_runtime_metrics", None)
        if not callable(metrics_getter):
            status["metrics"] = {}
            return status
        try:
            raw_metrics = metrics_getter()
        except (RuntimeError, OSError, ValueError, TypeError):
            status["metrics"] = {}
            return status
        metrics: dict[str, float] = {}
        if isinstance(raw_metrics, dict):
            allowed_prefixes = (
                "l5_total_",
                "l5_batch_",
                "l5_call_rate_",
                "l5_reject_count_by_reject_reason_",
            )
            for key, value in raw_metrics.items():
                key_str = str(key)
                if not key_str.startswith(allowed_prefixes):
                    continue
                if isinstance(value, (int, float)):
                    metrics[key_str] = float(value)
        status["metrics"] = metrics
        return status

    def _l3_quality_shadow_summary_snapshot(self) -> dict[str, object]:
        """L3 AST 품질 shadow 요약 스냅샷을 반환한다 (best-effort)."""
        getter = getattr(self._enrich_engine, "get_l3_quality_shadow_summary", None)
        if not callable(getter):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        try:
            summary = getter()
        except (RuntimeError, OSError, ValueError, TypeError):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        if not isinstance(summary, dict):
            return {"enabled": False, "sampled_files": 0, "shadow_eval_errors": 0}
        return dict(summary)

    def _on_watcher_signal(self, event_type: str, repo_root: str, relative_path: str, dest_path: str) -> None:
        """watcher cheap signal을 hotness tracker로 전달한다 (Phase 1 Baseline)."""
        del dest_path
        if not self._lsp_session_broker_enabled:
            return
        language = resolve_language_from_path(file_path=relative_path)
        scope_root = self._derive_hotness_scope_hint(repo_root=repo_root, relative_path=relative_path)
        self._watcher_hotness_tracker.record_fs_event(
            event_type=event_type,
            repo_root=repo_root,
            relative_path=relative_path,
            language=language,
            lsp_scope_root=scope_root,
        )

    def _invalidate_scope_caches_from_watcher_signal(self, repo_root: str, relative_path: str) -> None:
        """삭제/이동 이벤트가 유발한 scope cache invalidation signal을 처리한다."""
        invalidator = getattr(self._lsp_backend, "invalidate_scope_override_path", None)
        if callable(invalidator):
            try:
                invalidator(repo_root=repo_root, relative_path=relative_path)
            except (RuntimeError, OSError, ValueError, TypeError):
                ...
        planner = getattr(self._lsp_backend, "_lsp_scope_planner", None)
        planner_invalidate = getattr(planner, "invalidate_path", None) if planner is not None else None
        if callable(planner_invalidate):
            try:
                planner_invalidate(str((Path(repo_root) / relative_path).resolve()))
            except (RuntimeError, OSError, ValueError, TypeError):
                ...

    def _derive_hotness_scope_hint(self, *, repo_root: str, relative_path: str) -> str | None:
        """cheap signal용 scope 힌트(top-level fallback)."""
        normalized = normalize_repo_relative_path(relative_path)
        if normalized in {"", "."}:
            return str(Path(repo_root).resolve())
        first = normalized.split("/", 1)[0]
        if first in {"", "."}:
            return str(Path(repo_root).resolve())
        return str((Path(repo_root).resolve() / first).resolve())

    def _record_watcher_queue_overflow(self, repo_root: str | None, src_path: str) -> None:
        """watcher 큐 overflow를 기록한다."""
        now_iso = now_iso8601_utc()
        with self._metrics_lock:
            self._watcher_drop_count += 1
            self._watcher_overflow_count += 1
            self._watcher_last_overflow_at = now_iso
        self._error_policy.record_error_event(
            component="event_watcher",
            phase="watcher_overflow",
            severity="error",
            error_code="ERR_WATCHER_QUEUE_OVERFLOW",
            error_message="watcher queue overflow detected; recovery rescan scheduled",
            error_type="QueueFull",
            repo_root=repo_root,
            relative_path=src_path,
            job_id=None,
            attempt_count=0,
            context_data={"queue_max": self._watcher_queue_max},
            worker_name="watcher",
        )

    def _schedule_rescan_from_watcher(self, repo_root: str) -> None:
        """watcher overflow 복구 재스캔을 비동기로 예약한다."""
        self._ensure_watcher_rescan_worker_started()
        with self._watcher_rescan_lock:
            if repo_root in self._watcher_rescan_pending_roots:
                return
            self._watcher_rescan_pending_roots.add(repo_root)
        self._watcher_rescan_queue.put_nowait(repo_root)

    def _ensure_watcher_rescan_worker_started(self) -> None:
        """watcher overflow rescan 전용 워커를 필요 시 시작한다."""
        with self._watcher_rescan_lock:
            if self._watcher_rescan_thread is not None and self._watcher_rescan_thread.is_alive():
                return
            self._watcher_rescan_thread = threading.Thread(
                target=self._watcher_overflow_rescan_loop,
                daemon=True,
                name="watcher-overflow-rescan",
            )
            self._watcher_rescan_thread.start()

    def _watcher_overflow_rescan_loop(self) -> None:
        """watcher overflow로 예약된 repo 재스캔을 순차 처리한다."""
        while not self._stop_event.is_set():
            try:
                repo_root = self._watcher_rescan_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                _ = self.scan_once(repo_root)
            except CollectionError as exc:
                if self._handle_background_collection_error_proxy(
                    exc=exc,
                    phase="watcher_overflow_rescan",
                    worker_name="watcher_rescan",
                ):
                    return
            except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
                wrapped = CollectionError(
                    ErrorContext(
                        code="ERR_WATCHER_RESCAN_FAILED",
                        message=f"watcher overflow rescan 실패: {exc}",
                    )
                )
                if self._handle_background_collection_error_proxy(
                    exc=wrapped,
                    phase="watcher_overflow_rescan",
                    worker_name="watcher_rescan",
                ):
                    return
            finally:
                with self._watcher_rescan_lock:
                    self._watcher_rescan_pending_roots.discard(repo_root)

    def _record_watcher_file_race(self, repo_root: str, relative_path: str, reason: str) -> None:
        """watcher 경합성 파일 누락 이벤트를 저심각도 경고로 기록한다."""
        self._error_policy.record_error_event(
            component="event_watcher",
            phase="watcher_file_race",
            severity="warning",
            error_code="ERR_WATCHER_FILE_RACE",
            error_message="watcher 이벤트 처리 중 파일이 사라졌습니다",
            error_type="FileRaceCondition",
            repo_root=repo_root,
            relative_path=relative_path,
            job_id=None,
            attempt_count=0,
            context_data={"reason": reason},
            worker_name="watcher",
        )

    def list_files(self, repo_root: str, limit: int, prefix: str | None) -> list[dict[str, object]]:
        if limit <= 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_LIMIT', message='limit는 1 이상이어야 합니다'))
        rows = self._file_repo.list_files_by_scope(scope_repo_root=repo_root, limit=limit, prefix=prefix)
        if len(rows) == 0:
            # fanout 스캔 이전/혼합 상태에서는 module repo_root 기준 row만 존재할 수 있다.
            # 이 경우 기존 계약(list_files(repo_root))을 유지하기 위해 module 조회로 폴백한다.
            rows = self._file_repo.list_files(repo_root=repo_root, limit=limit, prefix=prefix)
        return [{'repo': item.repo, 'relative_path': item.relative_path, 'size_bytes': item.size_bytes, 'mtime_ns': item.mtime_ns, 'content_hash': item.content_hash, 'enrich_state': item.enrich_state} for item in rows]

    def read_file(self, repo_root: str, relative_path: str, offset: int, limit: int | None) -> FileReadResultDTO:
        if offset < 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_OFFSET', message='offset은 0 이상이어야 합니다'))
        if limit is not None and limit <= 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_LIMIT', message='limit는 1 이상이어야 합니다'))
        candidates = [item for item in self._file_repo.get_files_by_scope(scope_repo_root=repo_root, relative_path=relative_path, limit=16) if not item.is_deleted]
        if len(candidates) == 0:
            # fanout 스캔 이후 module-root 호출은 scope 조회가 비어도 repo_root 기준 단일 행이 존재할 수 있다.
            # list_files와 동일한 하위호환 계약을 위해 module repo_root 조회로 한 번 더 폴백한다.
            fallback = self._file_repo.get_file(repo_root=repo_root, relative_path=relative_path)
            if fallback is None or fallback.is_deleted:
                raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='파일 메타데이터를 찾을 수 없습니다'))
            row = fallback
        else:
            if len(candidates) > 1:
                # 혼합 마이그레이션 구간(기존 repo_root row + fanout row)에서는
                # 요청 repo_root와 정확히 일치하는 행을 우선 선택해야 기존 read 계약이 유지된다.
                exact_matches = [item for item in candidates if item.repo_root == repo_root]
                if len(exact_matches) == 1:
                    row = exact_matches[0]
                else:
                    raise CollectionError(ErrorContext(code='ERR_AMBIGUOUS_PATH_IN_SCOPE', message='scope 내 동일 relative_path가 여러 repo에 존재합니다'))
            else:
                row = candidates[0]
        try:
            # L2 본문 PK는 실제 파일이 저장된 module repo_root 기준이다.
            # scope repo_root(상위 fanout 루트)로 조회하면 L2 hit가 누락되어
            # 불필요한 FS fallback이 발생하므로 row.repo_root를 사용한다.
            body_text = self._body_repo.read_body_text(
                repo_root=row.repo_root,
                relative_path=relative_path,
                content_hash=row.content_hash,
            )
        except FileBodyDecodeError as exc:
            self._error_policy.record_error_event(component='file_collection_service', phase='read_file', severity='error', error_code='ERR_L2_BODY_CORRUPT', error_message=str(exc), error_type=type(exc).__name__, repo_root=repo_root, relative_path=relative_path, job_id=None, attempt_count=0, context_data={'content_hash': row.content_hash}, worker_name='http_read', stacktrace_text=traceback.format_exc())
            raise CollectionError(ErrorContext(code='ERR_L2_BODY_CORRUPT', message='L2 본문 데이터가 손상되어 읽을 수 없습니다')) from exc
        source = 'l2'
        if body_text is None:
            source = 'fs'
            file_path = Path(row.absolute_path)
            if not file_path.exists() or not file_path.is_file():
                raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='파일 시스템에서 파일을 찾을 수 없습니다'))
            decoded = decode_bytes_with_policy(file_path.read_bytes())
            body_text = decoded.text
        lines = body_text.splitlines()
        total_lines = len(lines)
        end_index = total_lines if limit is None else min(total_lines, offset + limit)
        sliced = lines[offset:end_index]
        next_offset = end_index if end_index < total_lines else None
        return FileReadResultDTO(relative_path=relative_path, content='\n'.join(sliced), start_line=offset + 1, end_line=end_index, source=source, total_lines=total_lines, is_truncated=next_offset is not None, next_offset=next_offset)

    def start_background(self) -> None:
        self._enrich_engine.reset_runtime_state()
        self._worker_state = 'running'
        self._runtime_manager.start_background()
        self._ensure_watcher_rescan_worker_started()
        self._l5_upgrade_watcher.start()
        for workspace in self._workspace_repo.list_all():
            if not workspace.is_active:
                continue
            try:
                self._l5_upgrade_watcher.trigger_startup(repo_root=workspace.path)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                log.exception("L5 startup reconciliation failed (repo=%s)", workspace.path)

    def stop_background(self) -> None:
        self._runtime_manager.stop_background()
        watcher_rescan_thread = self._watcher_rescan_thread
        if watcher_rescan_thread is not None:
            # watcher overflow rescan은 대형 repo에서 오래 걸릴 수 있다.
            # 중간에 반환하면 pending/queue 상태를 지운 뒤 재시작 시 동일 repo 중복 rescan이
            # 재예약될 수 있으므로, 현재 in-flight 작업이 끝나 worker가 종료될 때까지 대기한다.
            while watcher_rescan_thread.is_alive():
                watcher_rescan_thread.join(timeout=0.5)
        with self._watcher_rescan_lock:
            self._watcher_rescan_pending_roots.clear()
        while True:
            try:
                self._watcher_rescan_queue.get_nowait()
            except queue.Empty:
                break
        self._enrich_engine.shutdown()
        self._repo_support.shutdown_probe_executor()

    def reset_probe_state(self) -> None:
        """성능 측정/진단용으로 probe 상태를 초기화한다."""
        resetter = getattr(self._lsp_backend, "reset_probe_state", None)
        if callable(resetter):
            resetter()

    def reset_lsp_runtime(self) -> None:
        """성능 측정/진단용으로 LSP 런타임을 종료한다."""
        resetter = getattr(self._lsp_backend, "reset_lsp_runtime", None)
        if callable(resetter):
            resetter()

    def reset_lsp_unavailable_cache(self, repo_root: str | None = None, language: str | None = None) -> int:
        """LSP unavailable 캐시를 수동 초기화한다."""
        resetter = getattr(self._lsp_backend, "clear_unavailable_state", None)
        if not callable(resetter):
            return 0
        return int(resetter(repo_root=repo_root, language=language))

    def reset_runtime_state(self) -> None:
        """성능 측정/진단용으로 인메모리 런타임 상태를 초기화한다."""
        self._enrich_engine.reset_runtime_state()

    def set_l5_admission_mode(self, *, shadow_enabled: bool, enforced: bool) -> None:
        """L5 admission 모드를 런타임에서 동적으로 갱신한다."""
        setter = getattr(self._enrich_engine, "set_l5_admission_mode", None)
        if callable(setter):
            setter(shadow_enabled=shadow_enabled, enforced=enforced)

    def set_l3_quality_shadow_mode(
        self,
        *,
        enabled: bool,
        sample_rate: float,
        max_files: int,
        lang_allowlist: tuple[str, ...],
    ) -> None:
        """L3 quality shadow 모드를 런타임에서 동적으로 갱신한다."""
        setter = getattr(self._enrich_engine, "set_l3_quality_shadow_mode", None)
        if callable(setter):
            setter(
                enabled=enabled,
                sample_rate=sample_rate,
                max_files=max_files,
                lang_allowlist=lang_allowlist,
            )

    def get_l3_quality_shadow_mode(self) -> dict[str, object]:
        """L3 quality shadow 런타임 설정값을 반환한다."""
        getter = getattr(self._enrich_engine, "get_l3_quality_shadow_mode", None)
        if not callable(getter):
            return {"enabled": False, "sample_rate": 0.0, "max_files": 0, "lang_allowlist": ()}
        try:
            mode = getter()
        except (RuntimeError, OSError, ValueError, TypeError):
            return {"enabled": False, "sample_rate": 0.0, "max_files": 0, "lang_allowlist": ()}
        if not isinstance(mode, dict):
            return {"enabled": False, "sample_rate": 0.0, "max_files": 0, "lang_allowlist": ()}
        return {
            "enabled": bool(mode.get("enabled", False)),
            "sample_rate": float(mode.get("sample_rate", 0.0)),
            "max_files": int(mode.get("max_files", 0)),
            "lang_allowlist": tuple(
                str(item)
                for item in mode.get("lang_allowlist", ())
                if str(item).strip() != ""
            ),
        }

    @contextmanager
    def temporary_scan_exclude_globs(self, globs: tuple[str, ...]):
        """scan_once 동안만 추가 exclude globs를 적용한다 (perf 측정 전용)."""
        manager = getattr(self._repo_support, "temporary_extra_exclude_globs", None)
        if callable(manager):
            with manager(globs):
                yield
            return
        yield

    def list_error_events(self, limit: int, offset: int=0, repo_root: str | None=None, error_code: str | None=None) -> list[dict[str, object]]:
        if self._error_event_repo is None:
            return []
        items = self._error_event_repo.list_events(limit=limit, offset=offset, repo_root=repo_root, error_code=error_code)
        return [item.to_dict() for item in items]

    def get_error_event(self, event_id: str) -> dict[str, object] | None:
        if self._error_event_repo is None:
            return None
        item = self._error_event_repo.get_event(event_id=event_id)
        if item is None:
            return None
        return item.to_dict()

    def _is_deletion_hold_enabled(self) -> bool:
        return self._repo_support.is_deletion_hold_enabled()

    def _record_error_event(self, component: str, phase: str, severity: str, error_code: str, error_message: str, error_type: str, repo_root: str | None, relative_path: str | None, job_id: str | None, attempt_count: int, context_data: dict[str, object], worker_name: str='collection', stacktrace_text: str | None=None) -> None:
        self._error_policy.record_error_event(component=component, phase=phase, severity=severity, error_code=error_code, error_message=error_message, error_type=error_type, repo_root=repo_root, relative_path=relative_path, job_id=job_id, attempt_count=attempt_count, context_data=context_data, worker_name=worker_name, stacktrace_text=stacktrace_text)

    def _index_file_with_priority(self, repo_root: str, relative_path: str, priority: int, enqueue_source: str) -> None:
        if relative_path.strip() == '':
            raise CollectionError(ErrorContext(code='ERR_RELATIVE_PATH_REQUIRED', message='relative_path는 필수입니다'))
        root = Path(repo_root).expanduser().resolve()
        repo_identity = self._repo_support.resolve_repo_identity(str(root))
        file_path = (root / relative_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='대상 파일을 찾을 수 없습니다'))
        gitignore_spec = self._repo_support.load_gitignore_spec(root)
        if not self._repo_support.is_collectible(file_path=file_path, repo_root=root, gitignore_spec=gitignore_spec):
            # watcher 이벤트에서 정책 비대상 파일은 큐에 적재하지 않는다.
            return
        now_iso = now_iso8601_utc()
        content_bytes = file_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        l1_row = CollectedFileL1DTO(repo_id=repo_identity.repo_id, repo_root=str(root), scope_repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), absolute_path=str(file_path), repo_label=repo_identity.repo_label, mtime_ns=file_path.stat().st_mtime_ns, size_bytes=file_path.stat().st_size, content_hash=content_hash, is_deleted=False, last_seen_at=now_iso, updated_at=now_iso, enrich_state='PENDING')
        self._file_repo.upsert_file(l1_row)
        self._enrich_queue_repo.enqueue(repo_id=repo_identity.repo_id, repo_root=str(root), scope_repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), content_hash=content_hash, priority=priority, enqueue_source=enqueue_source, now_iso=now_iso)
        if self._candidate_index_sink is not None:
            self._candidate_index_sink.record_upsert(CandidateIndexChangeDTO(repo_id=repo_identity.repo_id, repo_root=str(root), scope_repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), absolute_path=str(file_path), content_hash=content_hash, mtime_ns=file_path.stat().st_mtime_ns, size_bytes=file_path.stat().st_size, event_source=enqueue_source, recorded_at=now_iso))

    def _assert_parent_alive(self, worker_name: str) -> None:
        if self._parent_alive_probe is None:
            return
        if self._parent_alive_probe():
            return
        raise CollectionError(ErrorContext(code='ERR_ORPHAN_DETECTED', message=f'고아 워커 감지: {worker_name}'))

    def _handle_background_collection_error_proxy(self, exc: CollectionError, phase: str, worker_name: str) -> bool:
        """오류 정책 컴포넌트로 background 오류 처리를 위임한다."""
        should_stop = self._error_policy.handle_background_collection_error(exc=exc, phase=phase, worker_name=worker_name)
        if should_stop:
            self._worker_state = 'failed'
        return should_stop

def build_default_file_collection_service(workspace_repo: WorkspaceRepository, file_repo: FileCollectionRepository, enrich_queue_repo: FileEnrichQueueRepository, body_repo: FileBodyRepository, lsp_repo: LspToolDataRepository, readiness_repo: ToolReadinessRepository, policy_repo: PipelinePolicyRepository | None=None, event_repo: PipelineJobEventRepository | None=None, error_event_repo: PipelineErrorEventRepository | None=None, candidate_index_sink: CandidateIndexSink | None=None, vector_index_sink: VectorIndexSink | None=None, retry_max_attempts: int=5, retry_backoff_base_sec: int=1, queue_poll_interval_ms: int=500, include_ext: tuple[str, ...] | None=None, exclude_globs: tuple[str, ...] | None=None, watcher_debounce_ms: int=300, run_mode: str='dev', parent_alive_probe: Callable[[], bool] | None=None, lsp_backend: LspExtractionBackend | None=None, persist_body_for_read: bool=True, l3_parallel_enabled: bool=True, l3_executor_max_workers: int=0, l3_recent_success_ttl_sec: int=120, l3_backpressure_on_interactive: bool=True, l3_backpressure_cooldown_ms: int=300, l3_supported_languages: tuple[str, ...] | None=None, lsp_probe_bootstrap_file_window: int=256, lsp_probe_bootstrap_top_k: int=3, lsp_probe_language_priority: tuple[str, ...]=("go:1.5", "java:1.4", "kotlin:1.3"), lsp_probe_l1_languages: tuple[str, ...]=("go", "java", "kotlin"), lsp_probe_scan_prewarm_enabled: bool=True, lsp_scope_java_markers: tuple[str, ...]=("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), lsp_scope_ts_markers: tuple[str, ...]=("tsconfig.json", "jsconfig.json", "package.json"), lsp_scope_vue_markers: tuple[str, ...]=("vue.config.js", "vite.config.ts", "package.json", "tsconfig.json"), lsp_scope_top_level_fallback: bool=True, lsp_scope_active_languages: tuple[str, ...] | None=None, lsp_session_broker_enabled: bool=True, lsp_hotness_event_window_sec: float=10.0, lsp_hotness_decay_window_sec: float=30.0, lsp_broker_backlog_min_share: float=0.2, lsp_broker_max_standby_sessions_per_lang: int=2, lsp_broker_max_standby_sessions_per_budget_group: int=2, lsp_broker_ts_vue_active_cap: int=2, lsp_broker_java_hot_lanes: int=1, lsp_broker_java_backlog_lanes: int=1, lsp_broker_java_sticky_ttl_sec: float=600.0, lsp_broker_java_switch_cooldown_sec: float=5.0, lsp_broker_java_min_lease_ms: int=1500, lsp_broker_ts_hot_lanes: int=1, lsp_broker_ts_backlog_lanes: int=1, lsp_broker_ts_sticky_ttl_sec: float=180.0, lsp_broker_ts_switch_cooldown_sec: float=2.0, lsp_broker_ts_min_lease_ms: int=500, lsp_broker_vue_hot_lanes: int=1, lsp_broker_vue_backlog_lanes: int=1, lsp_broker_vue_sticky_ttl_sec: float=240.0, lsp_broker_vue_switch_cooldown_sec: float=3.0, lsp_broker_vue_min_lease_ms: int=800, l5_call_rate_total_max: float=0.05, l5_call_rate_batch_max: float=0.01, l5_calls_per_min_per_lang_max: int=30, l5_tokens_per_10sec_global_max: int=120, l5_tokens_per_10sec_per_lang_max: int=30, l5_tokens_per_10sec_per_workspace_max: int=20, pipeline_l5_worker_count: int=2, l3_query_compile_ms_budget: float=10.0, l3_query_budget_ms: float=30.0, l3_tree_sitter_executor_mode: str="inline", l3_tree_sitter_subinterp_workers: int=4, l3_tree_sitter_subinterp_min_bytes: int=4096, l5_db_short_circuit_enabled: bool=True, tool_layer_repo: ToolDataLayerRepository | None=None, event_bus: object=None, l5_async_quality_upgrade_enabled: bool=True, l5_async_quality_upgrade_batch_size: int=50, l5_async_quality_upgrade_poll_interval_sec: float=5.0) -> CollectionRuntimePort:
    resolved_include_ext = include_ext if include_ext is not None else get_default_collection_extensions()
    resolved_exclude_globs = exclude_globs if exclude_globs is not None else DEFAULT_COLLECTION_EXCLUDE_GLOBS
    policy = CollectionPolicyDTO(include_ext=resolved_include_ext, exclude_globs=resolved_exclude_globs, max_file_size_bytes=512 * 1024, scan_interval_sec=180, max_enrich_batch=20, retry_max_attempts=retry_max_attempts, retry_backoff_base_sec=retry_backoff_base_sec, queue_poll_interval_ms=queue_poll_interval_ms)
    resolved_lsp_backend = lsp_backend if lsp_backend is not None else SolidLspExtractionBackend(LspHub())
    if isinstance(resolved_lsp_backend, SolidLspExtractionBackend):
        resolved_lsp_backend.configure_lsp_scope_planner(
            planner=LspScopePlanner(
                java_markers=lsp_scope_java_markers,
                ts_markers=lsp_scope_ts_markers,
                vue_markers=lsp_scope_vue_markers,
                top_level_fallback=lsp_scope_top_level_fallback,
            ),
            enabled=True,
        )
    repo_registry_repo = RepoRegistryRepository(file_repo.db_path)
    service = FileCollectionService(workspace_repo=workspace_repo, file_repo=file_repo, enrich_queue_repo=enrich_queue_repo, body_repo=body_repo, lsp_repo=lsp_repo, readiness_repo=readiness_repo, policy=policy, lsp_backend=resolved_lsp_backend, policy_repo=policy_repo, event_repo=event_repo, error_event_repo=error_event_repo, candidate_index_sink=candidate_index_sink, vector_index_sink=vector_index_sink, run_mode=run_mode, parent_alive_probe=parent_alive_probe, persist_body_for_read=persist_body_for_read, repo_registry_repo=repo_registry_repo, l3_parallel_enabled=l3_parallel_enabled, l3_executor_max_workers=l3_executor_max_workers, l3_recent_success_ttl_sec=l3_recent_success_ttl_sec, l3_backpressure_on_interactive=l3_backpressure_on_interactive, l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms, l3_supported_languages=l3_supported_languages, lsp_probe_bootstrap_file_window=lsp_probe_bootstrap_file_window, lsp_probe_bootstrap_top_k=lsp_probe_bootstrap_top_k, lsp_probe_language_priority=lsp_probe_language_priority, lsp_probe_l1_languages=lsp_probe_l1_languages, lsp_probe_scan_prewarm_enabled=lsp_probe_scan_prewarm_enabled, lsp_session_broker_enabled=lsp_session_broker_enabled, lsp_scope_active_languages=lsp_scope_active_languages, lsp_hotness_event_window_sec=lsp_hotness_event_window_sec, lsp_hotness_decay_window_sec=lsp_hotness_decay_window_sec, lsp_broker_backlog_min_share=lsp_broker_backlog_min_share, lsp_broker_max_standby_sessions_per_lang=lsp_broker_max_standby_sessions_per_lang, lsp_broker_max_standby_sessions_per_budget_group=lsp_broker_max_standby_sessions_per_budget_group, lsp_broker_ts_vue_active_cap=lsp_broker_ts_vue_active_cap, lsp_broker_java_hot_lanes=lsp_broker_java_hot_lanes, lsp_broker_java_backlog_lanes=lsp_broker_java_backlog_lanes, lsp_broker_java_sticky_ttl_sec=lsp_broker_java_sticky_ttl_sec, lsp_broker_java_switch_cooldown_sec=lsp_broker_java_switch_cooldown_sec, lsp_broker_java_min_lease_ms=lsp_broker_java_min_lease_ms, lsp_broker_ts_hot_lanes=lsp_broker_ts_hot_lanes, lsp_broker_ts_backlog_lanes=lsp_broker_ts_backlog_lanes, lsp_broker_ts_sticky_ttl_sec=lsp_broker_ts_sticky_ttl_sec, lsp_broker_ts_switch_cooldown_sec=lsp_broker_ts_switch_cooldown_sec, lsp_broker_ts_min_lease_ms=lsp_broker_ts_min_lease_ms, lsp_broker_vue_hot_lanes=lsp_broker_vue_hot_lanes, lsp_broker_vue_backlog_lanes=lsp_broker_vue_backlog_lanes, lsp_broker_vue_sticky_ttl_sec=lsp_broker_vue_sticky_ttl_sec, lsp_broker_vue_switch_cooldown_sec=lsp_broker_vue_switch_cooldown_sec, lsp_broker_vue_min_lease_ms=lsp_broker_vue_min_lease_ms, l5_call_rate_total_max=l5_call_rate_total_max, l5_call_rate_batch_max=l5_call_rate_batch_max, l5_calls_per_min_per_lang_max=l5_calls_per_min_per_lang_max, l5_tokens_per_10sec_global_max=l5_tokens_per_10sec_global_max, l5_tokens_per_10sec_per_lang_max=l5_tokens_per_10sec_per_lang_max, l5_tokens_per_10sec_per_workspace_max=l5_tokens_per_10sec_per_workspace_max, pipeline_l5_worker_count=pipeline_l5_worker_count, l3_query_compile_ms_budget=l3_query_compile_ms_budget, l3_query_budget_ms=l3_query_budget_ms, l3_tree_sitter_executor_mode=l3_tree_sitter_executor_mode, l3_tree_sitter_subinterp_workers=l3_tree_sitter_subinterp_workers, l3_tree_sitter_subinterp_min_bytes=l3_tree_sitter_subinterp_min_bytes, l5_db_short_circuit_enabled=l5_db_short_circuit_enabled, tool_layer_repo=tool_layer_repo, event_bus=event_bus, l5_async_quality_upgrade_enabled=l5_async_quality_upgrade_enabled, l5_async_quality_upgrade_batch_size=l5_async_quality_upgrade_batch_size, l5_async_quality_upgrade_poll_interval_sec=l5_async_quality_upgrade_poll_interval_sec)
    service._watcher_debounce_ms = max(50, watcher_debounce_ms)
    return service

def _is_scope_escalation_trigger_error(code: str, message: str) -> bool:
    """Phase1 baseline scope escalation taxonomy를 판정한다."""
    normalized_code = code.strip().upper()
    normalized_message = message.strip()
    lowered = normalized_message.lower()
    if normalized_code == "ERR_LSP_WORKSPACE_MISMATCH":
        return True
    if normalized_code == "ERR_CONFIG_INVALID":
        return True
    if normalized_code == "ERR_LSP_DOCUMENT_SYMBOL_FAILED":
        project_missing_patterns = (
            "no workspace contains",
            "project not found",
            "project model missing",
            "workspace contains",
        )
        return any(pattern in lowered for pattern in project_missing_patterns)
    return False


def _next_scope_level_for_escalation(current_scope_level: str | None) -> str | None:
    """module -> repo -> workspace 순으로 다음 escalation 단계를 반환한다."""
    level = (current_scope_level or "module").strip().lower()
    if level == "module":
        return "repo"
    if level == "repo":
        return "workspace"
    return None

def _parse_language_priority_weights(items: tuple[str, ...]) -> dict[Language, float]:
    """언어 우선순위 설정 문자열을 가중치 맵으로 파싱한다."""
    weights: dict[Language, float] = {}
    for item in items:
        raw = item.strip()
        if raw == "" or ":" not in raw:
            continue
        name, raw_weight = raw.split(":", 1)
        language = resolve_language_from_path(file_path=f"file.{name.strip().lower()}")
        if language is None:
            continue
        try:
            weight = max(0.1, float(raw_weight.strip()))
        except ValueError:
            continue
        weights[language] = weight
    return weights
