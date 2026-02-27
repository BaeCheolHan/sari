"""런타임 설정 모델을 정의한다."""

from dataclasses import dataclass
from pathlib import Path

from sari.core.config_fields import (
    _ConfigField,
    _build_core_fields,
    _build_early_fields,
    _build_extended_fields,
    _read_config_fields,
)
from sari.core.language.registry import get_default_collection_extensions, get_enabled_language_names

__all__ = [
    "AppConfig",
    "CollectionRuntimeConfigDTO",
    "LspHubRuntimeConfigDTO",
    "SearchRuntimeConfigDTO",
    "_ConfigField",
    "_read_config_fields",
    "_build_early_fields",
    "_build_core_fields",
    "_build_extended_fields",
]

DEFAULT_COLLECTION_EXCLUDE_GLOBS: tuple[str, ...] = (
    "**/.git/**",
    "**/bin/**",
    "**/generated-sources/**",
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.idea/**",
    "**/.vscode/**",
    "**/.gradle/**",
    "**/.next/**",
    "**/out/**",
    "**/coverage/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.cache/**",
)

@dataclass(frozen=True)
class CollectionRuntimeConfigDTO:
    """collection/service wiring에 필요한 설정 묶음."""

    pipeline_retry_max: int
    pipeline_backoff_base_sec: int
    queue_poll_interval_ms: int
    watcher_debounce_ms: int
    include_ext: tuple[str, ...]
    exclude_globs: tuple[str, ...]


@dataclass(frozen=True)
class LspHubRuntimeConfigDTO:
    """LspHub 생성에 필요한 설정 묶음."""

    request_timeout_sec: float
    max_instances_per_repo_language: int
    bulk_mode_enabled: bool
    bulk_max_instances_per_repo_language: int
    interactive_reserved_slots_per_repo_language: int
    interactive_timeout_sec: float
    lsp_global_soft_limit: int
    scale_out_hot_hits: int
    file_buffer_idle_ttl_sec: float
    file_buffer_max_open: int
    java_min_major: int
    max_concurrent_starts: int
    max_concurrent_l1_probes: int


@dataclass(frozen=True)
class SearchRuntimeConfigDTO:
    """search stack 생성에 필요한 설정 묶음."""

    candidate_backend: str
    candidate_fallback_scan: bool
    importance_kind_class: float
    importance_kind_function: float
    importance_kind_interface: float
    importance_kind_method: float
    importance_fan_in_weight: float
    importance_filename_exact_bonus: float
    importance_core_path_bonus: float
    importance_noisy_path_penalty: float
    importance_code_ext_bonus: float
    importance_noisy_ext_penalty: float
    importance_recency_24h_multiplier: float
    importance_recency_7d_multiplier: float
    importance_recency_30d_multiplier: float
    importance_normalize_mode: str
    importance_max_boost: float
    importance_core_path_tokens: tuple[str, ...]
    importance_noisy_path_tokens: tuple[str, ...]
    importance_code_extensions: tuple[str, ...]
    importance_noisy_extensions: tuple[str, ...]
    vector_enabled: bool
    vector_model_id: str
    vector_dim: int
    vector_candidate_k: int
    vector_rerank_k: int
    vector_blend_weight: float
    vector_min_similarity_threshold: float
    vector_max_boost: float
    vector_min_token_count_for_rerank: int
    vector_apply_to_item_types: tuple[str, ...]
    ranking_w_rrf: float
    ranking_w_importance: float
    ranking_w_vector: float
    ranking_w_hierarchy: float
    search_lsp_fallback_mode: str
    search_lsp_pressure_guard_enabled: bool
    search_lsp_pressure_pending_threshold: int
    search_lsp_pressure_timeout_threshold: int
    search_lsp_pressure_rejected_threshold: int
    search_lsp_recent_failure_cooldown_sec: float
    lsp_include_info_default: bool
    lsp_symbol_info_budget_sec: float


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전역 설정 DTO다."""

    # Core daemon/bootstrap
    db_path: Path
    host: str
    preferred_port: int
    max_port_scan: int
    stop_grace_sec: int

    # Candidate/search backend bootstrap
    candidate_backend: str = "tantivy"
    candidate_fallback_scan: bool = True

    # Pipeline worker runtime
    pipeline_retry_max: int = 5
    pipeline_backoff_base_sec: int = 1
    queue_poll_interval_ms: int = 100
    watcher_debounce_ms: int = 150
    collection_include_ext: tuple[str, ...] = get_default_collection_extensions()
    collection_exclude_globs: tuple[str, ...] = DEFAULT_COLLECTION_EXCLUDE_GLOBS
    pipeline_worker_count: int = 4
    pipeline_l5_worker_count: int = 2
    pipeline_l3_p95_threshold_ms: int = 180_000
    pipeline_dead_ratio_threshold_bps: int = 10
    pipeline_alert_window_sec: int = 300
    pipeline_auto_tick_interval_sec: int = 5

    # Collection/L3 runtime
    l3_parallel_enabled: bool = True
    run_mode: str = "prod"
    daemon_heartbeat_interval_sec: int = 2
    daemon_stale_timeout_sec: int = 15
    lsp_request_timeout_sec: float = 20.0
    lsp_max_instances_per_repo_language: int = 3
    lsp_bulk_mode_enabled: bool = True
    lsp_bulk_max_instances_per_repo_language: int = 4
    lsp_interactive_reserved_slots_per_repo_language: int = 1
    lsp_interactive_timeout_sec: float = 4.0
    lsp_symbol_info_budget_sec: float = 10.0
    lsp_include_info_default: bool = False
    lsp_global_soft_limit: int = 0
    lsp_scale_out_hot_hits: int = 8
    l3_executor_max_workers: int = 0
    l3_recent_success_ttl_sec: int = 120
    l3_backpressure_on_interactive: bool = True
    l3_backpressure_cooldown_ms: int = 300
    l3_supported_languages: tuple[str, ...] = get_enabled_language_names()

    # LSP runtime admission/probe knobs
    lsp_file_buffer_idle_ttl_sec: float = 20.0
    lsp_file_buffer_max_open: int = 512
    lsp_java_min_major: int = 17
    lsp_probe_timeout_default_sec: float = 20.0
    lsp_probe_timeout_go_sec: float = 45.0
    lsp_probe_workers: int = 8
    lsp_probe_l1_workers: int = 4
    lsp_probe_force_join_ms: int = 300
    lsp_probe_warming_retry_sec: int = 5
    lsp_probe_warming_threshold: int = 6
    lsp_probe_permanent_backoff_sec: int = 1800
    lsp_probe_bootstrap_file_window: int = 256
    lsp_probe_bootstrap_top_k: int = 3
    lsp_probe_language_priority: tuple[str, ...] = ("go:1.5", "java:1.4", "kotlin:1.3")
    lsp_probe_l1_languages: tuple[str, ...] = ("go", "java", "kotlin", "py", "rs", "ts", "js")

    # LSP scope planner
    lsp_scope_planner_enabled: bool = True
    lsp_scope_planner_shadow_mode: bool = True
    lsp_scope_java_markers: tuple[str, ...] = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    lsp_scope_ts_markers: tuple[str, ...] = ("tsconfig.json", "jsconfig.json", "package.json")
    lsp_scope_vue_markers: tuple[str, ...] = ("vue.config.js", "vite.config.ts", "package.json", "tsconfig.json")
    lsp_scope_top_level_fallback: bool = True
    lsp_scope_active_languages: tuple[str, ...] = ()

    # LSP session broker policy
    lsp_session_broker_enabled: bool = True
    lsp_session_broker_metrics_enabled: bool = True
    lsp_broker_optional_scaffolding_enabled: bool = False
    lsp_broker_batch_throughput_mode_enabled: bool = False
    lsp_broker_batch_throughput_pending_threshold: int = 4
    lsp_broker_batch_disable_java_probe: bool = False
    lsp_hotness_event_window_sec: float = 10.0
    lsp_hotness_decay_window_sec: float = 30.0
    lsp_broker_backlog_min_share: float = 0.2
    lsp_broker_max_standby_sessions_per_lang: int = 2
    lsp_broker_max_standby_sessions_per_budget_group: int = 2
    lsp_broker_ts_vue_active_cap: int = 2
    lsp_broker_java_hot_lanes: int = 1
    lsp_broker_java_backlog_lanes: int = 1
    lsp_broker_java_sticky_ttl_sec: float = 600.0
    lsp_broker_java_switch_cooldown_sec: float = 5.0
    lsp_broker_java_min_lease_ms: int = 1500
    lsp_broker_ts_hot_lanes: int = 1
    lsp_broker_ts_backlog_lanes: int = 1
    lsp_broker_ts_sticky_ttl_sec: float = 180.0
    lsp_broker_ts_switch_cooldown_sec: float = 2.0
    lsp_broker_ts_min_lease_ms: int = 500
    lsp_broker_vue_hot_lanes: int = 1
    lsp_broker_vue_backlog_lanes: int = 1
    lsp_broker_vue_sticky_ttl_sec: float = 240.0
    lsp_broker_vue_switch_cooldown_sec: float = 3.0
    lsp_broker_vue_min_lease_ms: int = 800
    lsp_max_concurrent_starts: int = 4
    lsp_max_concurrent_l1_probes: int = 4

    # Process lifecycle safety
    orphan_ppid_check_interval_sec: int = 1
    shutdown_join_timeout_sec: int = 2

    # Search ranking/importance profile
    importance_kind_class: float = 600.0
    importance_kind_function: float = 500.0
    importance_kind_interface: float = 450.0
    importance_kind_method: float = 350.0
    importance_fan_in_weight: float = 24.0
    importance_filename_exact_bonus: float = 1.0
    importance_core_path_bonus: float = 0.6
    importance_noisy_path_penalty: float = -0.7
    importance_code_ext_bonus: float = 0.3
    importance_noisy_ext_penalty: float = -1.0
    importance_recency_24h_multiplier: float = 1.5
    importance_recency_7d_multiplier: float = 1.3
    importance_recency_30d_multiplier: float = 1.1
    importance_normalize_mode: str = "log1p"
    importance_max_boost: float = 200.0
    importance_core_path_tokens: tuple[str, ...] = ("src", "app", "core")
    importance_noisy_path_tokens: tuple[str, ...] = ("test", "tests", "build", "dist")
    importance_code_extensions: tuple[str, ...] = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".kts", ".go", ".rs")
    importance_noisy_extensions: tuple[str, ...] = (".lock", ".min.js")

    # Vector search profile
    vector_enabled: bool = False
    vector_model_id: str = "hashbow-v1"
    vector_dim: int = 128
    vector_candidate_k: int = 50
    vector_rerank_k: int = 20
    vector_blend_weight: float = 0.2
    vector_min_similarity_threshold: float = 0.15
    vector_max_boost: float = 0.2
    vector_min_token_count_for_rerank: int = 2
    vector_apply_to_item_types: tuple[str, ...] = ("symbol", "file")
    ranking_w_rrf: float = 0.55
    ranking_w_importance: float = 0.30
    ranking_w_vector: float = 0.15
    ranking_w_hierarchy: float = 0.15

    # Search-time LSP guard
    search_lsp_fallback_mode: str = "normal"
    search_lsp_pressure_guard_enabled: bool = True
    search_lsp_pressure_pending_threshold: int = 1
    search_lsp_pressure_timeout_threshold: int = 1
    search_lsp_pressure_rejected_threshold: int = 1
    search_lsp_recent_failure_cooldown_sec: float = 5.0

    # L5 admission/token budget
    l5_call_rate_total_max: float = 0.10
    l5_call_rate_batch_max: float = 0.05
    l5_calls_per_min_per_lang_max: int = 60
    l5_tokens_per_10sec_global_max: int = 240
    l5_tokens_per_10sec_per_lang_max: int = 60
    l5_tokens_per_10sec_per_workspace_max: int = 20

    # L3/L5 subinterpreter execution knobs
    l3_query_compile_cache_enabled: bool = True
    l3_query_compile_ms_budget: float = 10.0
    l3_query_budget_ms: float = 30.0
    l3_tree_sitter_executor_mode: str = "subinterp"
    l3_tree_sitter_subinterp_workers: int = 4
    l3_tree_sitter_subinterp_min_bytes: int = 2048
    l3_asset_mode: str = "shadow"
    l3_asset_lang_allowlist: tuple[str, ...] = ()
    l5_db_short_circuit_enabled: bool = True
    l5_db_short_circuit_log_miss_reason: bool = True
    l5_symbol_normalizer_executor_mode: str = "subinterp"
    l5_symbol_normalizer_subinterp_workers: int = 2
    l5_symbol_normalizer_subinterp_min_symbols: int = 100

    # MCP serving/runtime behavior
    mcp_forward_to_daemon: bool = False
    mcp_daemon_autostart: bool = True
    mcp_daemon_timeout_sec: float = 2.0
    mcp_search_call_timeout_sec: float = 0.0
    mcp_read_call_timeout_sec: float = 0.0
    strict_protocol: bool = False
    stabilization_enabled: bool = True
    http_bg_proxy_enabled: bool = False
    http_bg_proxy_target: str = ""

    def collection_config(self) -> CollectionRuntimeConfigDTO:
        """collection/service wiring에 사용할 설정 DTO를 반환한다."""
        return CollectionRuntimeConfigDTO(
            pipeline_retry_max=self.pipeline_retry_max,
            pipeline_backoff_base_sec=self.pipeline_backoff_base_sec,
            queue_poll_interval_ms=self.queue_poll_interval_ms,
            watcher_debounce_ms=self.watcher_debounce_ms,
            include_ext=self.collection_include_ext,
            exclude_globs=self.collection_exclude_globs,
        )

    def lsp_hub_config(self) -> LspHubRuntimeConfigDTO:
        """LspHub 생성용 설정 DTO를 반환한다."""
        return LspHubRuntimeConfigDTO(
            request_timeout_sec=self.lsp_request_timeout_sec,
            max_instances_per_repo_language=self.lsp_max_instances_per_repo_language,
            bulk_mode_enabled=self.lsp_bulk_mode_enabled,
            bulk_max_instances_per_repo_language=self.lsp_bulk_max_instances_per_repo_language,
            interactive_reserved_slots_per_repo_language=self.lsp_interactive_reserved_slots_per_repo_language,
            interactive_timeout_sec=self.lsp_interactive_timeout_sec,
            lsp_global_soft_limit=self.lsp_global_soft_limit,
            scale_out_hot_hits=self.lsp_scale_out_hot_hits,
            file_buffer_idle_ttl_sec=self.lsp_file_buffer_idle_ttl_sec,
            file_buffer_max_open=self.lsp_file_buffer_max_open,
            java_min_major=self.lsp_java_min_major,
            max_concurrent_starts=self.lsp_max_concurrent_starts,
            max_concurrent_l1_probes=self.lsp_max_concurrent_l1_probes,
        )

    def search_config(self) -> SearchRuntimeConfigDTO:
        """search stack 생성용 설정 DTO를 반환한다."""
        return SearchRuntimeConfigDTO(
            candidate_backend=self.candidate_backend,
            candidate_fallback_scan=self.candidate_fallback_scan,
            importance_kind_class=self.importance_kind_class,
            importance_kind_function=self.importance_kind_function,
            importance_kind_interface=self.importance_kind_interface,
            importance_kind_method=self.importance_kind_method,
            importance_fan_in_weight=self.importance_fan_in_weight,
            importance_filename_exact_bonus=self.importance_filename_exact_bonus,
            importance_core_path_bonus=self.importance_core_path_bonus,
            importance_noisy_path_penalty=self.importance_noisy_path_penalty,
            importance_code_ext_bonus=self.importance_code_ext_bonus,
            importance_noisy_ext_penalty=self.importance_noisy_ext_penalty,
            importance_recency_24h_multiplier=self.importance_recency_24h_multiplier,
            importance_recency_7d_multiplier=self.importance_recency_7d_multiplier,
            importance_recency_30d_multiplier=self.importance_recency_30d_multiplier,
            importance_normalize_mode=self.importance_normalize_mode,
            importance_max_boost=self.importance_max_boost,
            importance_core_path_tokens=self.importance_core_path_tokens,
            importance_noisy_path_tokens=self.importance_noisy_path_tokens,
            importance_code_extensions=self.importance_code_extensions,
            importance_noisy_extensions=self.importance_noisy_extensions,
            vector_enabled=self.vector_enabled,
            vector_model_id=self.vector_model_id,
            vector_dim=self.vector_dim,
            vector_candidate_k=self.vector_candidate_k,
            vector_rerank_k=self.vector_rerank_k,
            vector_blend_weight=self.vector_blend_weight,
            vector_min_similarity_threshold=self.vector_min_similarity_threshold,
            vector_max_boost=self.vector_max_boost,
            vector_min_token_count_for_rerank=self.vector_min_token_count_for_rerank,
            vector_apply_to_item_types=self.vector_apply_to_item_types,
            ranking_w_rrf=self.ranking_w_rrf,
            ranking_w_importance=self.ranking_w_importance,
            ranking_w_vector=self.ranking_w_vector,
            ranking_w_hierarchy=self.ranking_w_hierarchy,
            search_lsp_fallback_mode=self.search_lsp_fallback_mode,
            search_lsp_pressure_guard_enabled=self.search_lsp_pressure_guard_enabled,
            search_lsp_pressure_pending_threshold=self.search_lsp_pressure_pending_threshold,
            search_lsp_pressure_timeout_threshold=self.search_lsp_pressure_timeout_threshold,
            search_lsp_pressure_rejected_threshold=self.search_lsp_pressure_rejected_threshold,
            search_lsp_recent_failure_cooldown_sec=self.search_lsp_recent_failure_cooldown_sec,
            lsp_include_info_default=self.lsp_include_info_default,
            lsp_symbol_info_budget_sec=self.lsp_symbol_info_budget_sec,
        )

    @classmethod
    def default(cls) -> "AppConfig":
        """기본 설정값으로 구성 객체를 생성한다."""
        from sari.core.config_loader import build_default_config

        return build_default_config(cls)
