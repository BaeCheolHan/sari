"""런타임 설정을 정의한다."""

import logging
import os
import json
from dataclasses import dataclass
from pathlib import Path

from sari.core.language.registry import get_default_collection_extensions, get_enabled_language_names

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

    db_path: Path
    host: str
    preferred_port: int
    max_port_scan: int
    stop_grace_sec: int
    candidate_backend: str = "tantivy"
    candidate_fallback_scan: bool = True
    pipeline_retry_max: int = 5
    pipeline_backoff_base_sec: int = 1
    queue_poll_interval_ms: int = 500
    watcher_debounce_ms: int = 300
    collection_include_ext: tuple[str, ...] = get_default_collection_extensions()
    collection_exclude_globs: tuple[str, ...] = DEFAULT_COLLECTION_EXCLUDE_GLOBS
    pipeline_worker_count: int = 4
    pipeline_l3_p95_threshold_ms: int = 180_000
    pipeline_dead_ratio_threshold_bps: int = 10
    pipeline_alert_window_sec: int = 300
    pipeline_auto_tick_interval_sec: int = 5
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
    lsp_interactive_queue_max: int = 256
    lsp_symbol_info_budget_sec: float = 10.0
    lsp_include_info_default: bool = False
    lsp_global_soft_limit: int = 0
    lsp_scale_out_hot_hits: int = 24
    l3_executor_max_workers: int = 0
    l3_recent_success_ttl_sec: int = 120
    l3_backpressure_on_interactive: bool = True
    l3_backpressure_cooldown_ms: int = 300
    l3_supported_languages: tuple[str, ...] = get_enabled_language_names()
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
    orphan_ppid_check_interval_sec: int = 1
    shutdown_join_timeout_sec: int = 2
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
    search_lsp_fallback_mode: str = "normal"
    search_lsp_pressure_guard_enabled: bool = True
    search_lsp_pressure_pending_threshold: int = 1
    search_lsp_pressure_timeout_threshold: int = 1
    search_lsp_pressure_rejected_threshold: int = 1
    search_lsp_recent_failure_cooldown_sec: float = 5.0
    l5_call_rate_total_max: float = 0.05
    l5_call_rate_batch_max: float = 0.01
    l5_calls_per_min_per_lang_max: int = 30
    l5_tokens_per_10sec_global_max: int = 120
    l5_tokens_per_10sec_per_lang_max: int = 30
    l5_tokens_per_10sec_per_workspace_max: int = 20
    l3_query_compile_cache_enabled: bool = True
    l3_query_compile_ms_budget: float = 10.0
    l3_query_budget_ms: float = 30.0
    l3_tree_sitter_executor_mode: str = "inline"
    l3_tree_sitter_subinterp_workers: int = 4
    l3_tree_sitter_subinterp_min_bytes: int = 4096
    l3_asset_mode: str = "shadow"
    l3_asset_manifest_path: str = "src/sari/services/collection/assets/manifest.json"
    l3_asset_lang_allowlist: tuple[str, ...] = ()
    l5_db_short_circuit_enabled: bool = True
    l5_db_short_circuit_log_miss_reason: bool = True
    l5_symbol_normalizer_executor_mode: str = "inline"
    l5_symbol_normalizer_subinterp_workers: int = 2
    l5_symbol_normalizer_subinterp_min_symbols: int = 200
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
        file_config = _load_user_config()
        early_raw_values = _read_config_fields(
            file_config=file_config,
            fields=_build_early_fields(),
        )
        db_path_raw = early_raw_values["db_path_raw"]
        db_path = Path(db_path_raw).expanduser() if db_path_raw != "" else Path.home() / ".local" / "share" / "sari-v2" / "state.db"
        backend = early_raw_values["backend_raw"]
        if backend not in {"tantivy", "scan"}:
            backend = "tantivy"
        fallback_flag = early_raw_values["fallback_flag"]
        core_raw_values = _read_config_fields(
            file_config=file_config,
            fields=_build_core_fields(file_config=file_config, defaults=cls),
        )
        retry_max_raw = core_raw_values["retry_max_raw"]
        backoff_raw = core_raw_values["backoff_raw"]
        poll_raw = core_raw_values["poll_raw"]
        debounce_raw = core_raw_values["debounce_raw"]
        worker_raw = core_raw_values["worker_raw"]
        p95_raw = core_raw_values["p95_raw"]
        dead_ratio_raw = core_raw_values["dead_ratio_raw"]
        alert_window_raw = core_raw_values["alert_window_raw"]
        auto_tick_raw = core_raw_values["auto_tick_raw"]
        l3_parallel_enabled_raw = core_raw_values["l3_parallel_enabled_raw"]
        run_mode_raw = core_raw_values["run_mode_raw"]
        heartbeat_raw = core_raw_values["heartbeat_raw"]
        stale_timeout_raw = core_raw_values["stale_timeout_raw"]
        lsp_timeout_raw = core_raw_values["lsp_timeout_raw"]
        lsp_max_per_repo_lang_raw = core_raw_values["lsp_max_per_repo_lang_raw"]
        lsp_bulk_mode_enabled_raw = core_raw_values["lsp_bulk_mode_enabled_raw"]
        lsp_bulk_max_per_repo_lang_raw = core_raw_values["lsp_bulk_max_per_repo_lang_raw"]
        lsp_interactive_reserved_slots_raw = core_raw_values["lsp_interactive_reserved_slots_raw"]
        lsp_interactive_timeout_raw = core_raw_values["lsp_interactive_timeout_raw"]
        lsp_interactive_queue_max_raw = core_raw_values["lsp_interactive_queue_max_raw"]
        lsp_symbol_info_budget_raw = core_raw_values["lsp_symbol_info_budget_raw"]
        lsp_include_info_default_raw = core_raw_values["lsp_include_info_default_raw"]
        lsp_global_soft_limit_raw = core_raw_values["lsp_global_soft_limit_raw"]
        l3_executor_max_workers_raw = core_raw_values["l3_executor_max_workers_raw"]
        l3_recent_success_ttl_raw = core_raw_values["l3_recent_success_ttl_raw"]
        l3_backpressure_on_interactive_raw = core_raw_values["l3_backpressure_on_interactive_raw"]
        l3_backpressure_cooldown_ms_raw = core_raw_values["l3_backpressure_cooldown_ms_raw"]
        l3_supported_languages_raw = core_raw_values["l3_supported_languages_raw"]
        lsp_scale_out_hot_hits_raw = core_raw_values["lsp_scale_out_hot_hits_raw"]
        extended_raw_values = _read_config_fields(
            file_config=file_config,
            fields=_build_extended_fields(file_config=file_config, defaults=cls),
        )
        lsp_file_buffer_idle_ttl_raw = extended_raw_values["lsp_file_buffer_idle_ttl_raw"]
        lsp_file_buffer_max_open_raw = extended_raw_values["lsp_file_buffer_max_open_raw"]
        lsp_java_min_major_raw = extended_raw_values["lsp_java_min_major_raw"]
        lsp_probe_timeout_default_raw = extended_raw_values["lsp_probe_timeout_default_raw"]
        lsp_probe_timeout_go_raw = extended_raw_values["lsp_probe_timeout_go_raw"]
        lsp_probe_workers_raw = extended_raw_values["lsp_probe_workers_raw"]
        lsp_probe_l1_workers_raw = extended_raw_values["lsp_probe_l1_workers_raw"]
        lsp_probe_force_join_ms_raw = extended_raw_values["lsp_probe_force_join_ms_raw"]
        lsp_probe_warming_retry_sec_raw = extended_raw_values["lsp_probe_warming_retry_sec_raw"]
        lsp_probe_warming_threshold_raw = extended_raw_values["lsp_probe_warming_threshold_raw"]
        lsp_probe_permanent_backoff_sec_raw = extended_raw_values["lsp_probe_permanent_backoff_sec_raw"]
        lsp_probe_bootstrap_file_window_raw = extended_raw_values["lsp_probe_bootstrap_file_window_raw"]
        lsp_probe_bootstrap_top_k_raw = extended_raw_values["lsp_probe_bootstrap_top_k_raw"]
        lsp_probe_language_priority_raw = extended_raw_values["lsp_probe_language_priority_raw"]
        lsp_probe_l1_languages_raw = extended_raw_values["lsp_probe_l1_languages_raw"]
        lsp_scope_planner_enabled_raw = extended_raw_values["lsp_scope_planner_enabled_raw"]
        lsp_scope_planner_shadow_mode_raw = extended_raw_values["lsp_scope_planner_shadow_mode_raw"]
        lsp_scope_java_markers_raw = extended_raw_values["lsp_scope_java_markers_raw"]
        lsp_scope_ts_markers_raw = extended_raw_values["lsp_scope_ts_markers_raw"]
        lsp_scope_vue_markers_raw = extended_raw_values["lsp_scope_vue_markers_raw"]
        lsp_scope_top_level_fallback_raw = extended_raw_values["lsp_scope_top_level_fallback_raw"]
        lsp_scope_active_languages_raw = extended_raw_values["lsp_scope_active_languages_raw"]
        lsp_session_broker_enabled_raw = extended_raw_values["lsp_session_broker_enabled_raw"]
        lsp_session_broker_metrics_enabled_raw = extended_raw_values["lsp_session_broker_metrics_enabled_raw"]
        lsp_broker_optional_scaffolding_enabled_raw = extended_raw_values["lsp_broker_optional_scaffolding_enabled_raw"]
        lsp_broker_batch_throughput_mode_enabled_raw = extended_raw_values["lsp_broker_batch_throughput_mode_enabled_raw"]
        lsp_broker_batch_throughput_pending_threshold_raw = extended_raw_values["lsp_broker_batch_throughput_pending_threshold_raw"]
        lsp_broker_batch_disable_java_probe_raw = extended_raw_values["lsp_broker_batch_disable_java_probe_raw"]
        lsp_hotness_event_window_sec_raw = extended_raw_values["lsp_hotness_event_window_sec_raw"]
        lsp_hotness_decay_window_sec_raw = extended_raw_values["lsp_hotness_decay_window_sec_raw"]
        lsp_broker_backlog_min_share_raw = extended_raw_values["lsp_broker_backlog_min_share_raw"]
        lsp_broker_max_standby_sessions_per_lang_raw = extended_raw_values["lsp_broker_max_standby_sessions_per_lang_raw"]
        lsp_broker_max_standby_sessions_per_budget_group_raw = extended_raw_values["lsp_broker_max_standby_sessions_per_budget_group_raw"]
        lsp_broker_ts_vue_active_cap_raw = extended_raw_values["lsp_broker_ts_vue_active_cap_raw"]
        lsp_broker_java_hot_lanes_raw = extended_raw_values["lsp_broker_java_hot_lanes_raw"]
        lsp_broker_java_backlog_lanes_raw = extended_raw_values["lsp_broker_java_backlog_lanes_raw"]
        lsp_broker_java_sticky_ttl_sec_raw = extended_raw_values["lsp_broker_java_sticky_ttl_sec_raw"]
        lsp_broker_java_switch_cooldown_sec_raw = extended_raw_values["lsp_broker_java_switch_cooldown_sec_raw"]
        lsp_broker_java_min_lease_ms_raw = extended_raw_values["lsp_broker_java_min_lease_ms_raw"]
        lsp_broker_ts_hot_lanes_raw = extended_raw_values["lsp_broker_ts_hot_lanes_raw"]
        lsp_broker_ts_backlog_lanes_raw = extended_raw_values["lsp_broker_ts_backlog_lanes_raw"]
        lsp_broker_ts_sticky_ttl_sec_raw = extended_raw_values["lsp_broker_ts_sticky_ttl_sec_raw"]
        lsp_broker_ts_switch_cooldown_sec_raw = extended_raw_values["lsp_broker_ts_switch_cooldown_sec_raw"]
        lsp_broker_ts_min_lease_ms_raw = extended_raw_values["lsp_broker_ts_min_lease_ms_raw"]
        lsp_broker_vue_hot_lanes_raw = extended_raw_values["lsp_broker_vue_hot_lanes_raw"]
        lsp_broker_vue_backlog_lanes_raw = extended_raw_values["lsp_broker_vue_backlog_lanes_raw"]
        lsp_broker_vue_sticky_ttl_sec_raw = extended_raw_values["lsp_broker_vue_sticky_ttl_sec_raw"]
        lsp_broker_vue_switch_cooldown_sec_raw = extended_raw_values["lsp_broker_vue_switch_cooldown_sec_raw"]
        lsp_broker_vue_min_lease_ms_raw = extended_raw_values["lsp_broker_vue_min_lease_ms_raw"]
        lsp_max_concurrent_starts_raw = extended_raw_values["lsp_max_concurrent_starts_raw"]
        lsp_max_concurrent_l1_probes_raw = extended_raw_values["lsp_max_concurrent_l1_probes_raw"]
        orphan_check_raw = extended_raw_values["orphan_check_raw"]
        shutdown_join_raw = extended_raw_values["shutdown_join_raw"]
        vector_enabled_raw = extended_raw_values["vector_enabled_raw"]
        vector_model_id = str(file_config.get("vector_model_id", "hashbow-v1")).strip()
        vector_dim_raw = extended_raw_values["vector_dim_raw"]
        vector_candidate_raw = extended_raw_values["vector_candidate_raw"]
        vector_rerank_raw = extended_raw_values["vector_rerank_raw"]
        vector_blend_raw = extended_raw_values["vector_blend_raw"]
        vector_min_similarity_raw = extended_raw_values["vector_min_similarity_raw"]
        vector_max_boost_raw = extended_raw_values["vector_max_boost_raw"]
        vector_min_token_raw = extended_raw_values["vector_min_token_raw"]
        importance_normalize_mode = extended_raw_values["importance_normalize_mode"]
        importance_max_boost_raw = extended_raw_values["importance_max_boost_raw"]
        ranking_w_rrf_raw = extended_raw_values["ranking_w_rrf_raw"]
        ranking_w_importance_raw = extended_raw_values["ranking_w_importance_raw"]
        ranking_w_vector_raw = extended_raw_values["ranking_w_vector_raw"]
        ranking_w_hierarchy_raw = extended_raw_values["ranking_w_hierarchy_raw"]
        search_lsp_fallback_mode_raw = extended_raw_values["search_lsp_fallback_mode_raw"]
        search_lsp_pressure_guard_enabled_raw = extended_raw_values["search_lsp_pressure_guard_enabled_raw"]
        search_lsp_pressure_pending_threshold_raw = extended_raw_values["search_lsp_pressure_pending_threshold_raw"]
        search_lsp_pressure_timeout_threshold_raw = extended_raw_values["search_lsp_pressure_timeout_threshold_raw"]
        search_lsp_pressure_rejected_threshold_raw = extended_raw_values["search_lsp_pressure_rejected_threshold_raw"]
        search_lsp_recent_failure_cooldown_sec_raw = extended_raw_values["search_lsp_recent_failure_cooldown_sec_raw"]
        l5_call_rate_total_max_raw = extended_raw_values["l5_call_rate_total_max_raw"]
        l5_call_rate_batch_max_raw = extended_raw_values["l5_call_rate_batch_max_raw"]
        l5_calls_per_min_per_lang_max_raw = extended_raw_values["l5_calls_per_min_per_lang_max_raw"]
        l5_tokens_per_10sec_global_max_raw = extended_raw_values["l5_tokens_per_10sec_global_max_raw"]
        l5_tokens_per_10sec_per_lang_max_raw = extended_raw_values["l5_tokens_per_10sec_per_lang_max_raw"]
        l5_tokens_per_10sec_per_workspace_max_raw = extended_raw_values["l5_tokens_per_10sec_per_workspace_max_raw"]
        l3_query_compile_cache_enabled_raw = extended_raw_values["l3_query_compile_cache_enabled_raw"]
        l3_query_compile_ms_budget_raw = extended_raw_values["l3_query_compile_ms_budget_raw"]
        l3_query_budget_ms_raw = extended_raw_values["l3_query_budget_ms_raw"]
        l3_asset_mode_raw = extended_raw_values["l3_asset_mode_raw"]
        l3_asset_manifest_path = extended_raw_values["l3_asset_manifest_path"]
        l3_asset_lang_allowlist_raw = extended_raw_values["l3_asset_lang_allowlist_raw"]
        l5_db_short_circuit_enabled_raw = extended_raw_values["l5_db_short_circuit_enabled_raw"]
        l5_db_short_circuit_log_miss_reason_raw = extended_raw_values["l5_db_short_circuit_log_miss_reason_raw"]
        l3_tree_sitter_executor_mode_raw = extended_raw_values["l3_tree_sitter_executor_mode_raw"]
        l3_tree_sitter_subinterp_workers_raw = extended_raw_values["l3_tree_sitter_subinterp_workers_raw"]
        l3_tree_sitter_subinterp_min_bytes_raw = extended_raw_values["l3_tree_sitter_subinterp_min_bytes_raw"]
        l5_symbol_normalizer_executor_mode_raw = extended_raw_values["l5_symbol_normalizer_executor_mode_raw"]
        l5_symbol_normalizer_subinterp_workers_raw = extended_raw_values["l5_symbol_normalizer_subinterp_workers_raw"]
        l5_symbol_normalizer_subinterp_min_symbols_raw = extended_raw_values["l5_symbol_normalizer_subinterp_min_symbols_raw"]
        mcp_forward_to_daemon_raw = extended_raw_values["mcp_forward_to_daemon_raw"]
        mcp_daemon_autostart_raw = extended_raw_values["mcp_daemon_autostart_raw"]
        mcp_daemon_timeout_raw = extended_raw_values["mcp_daemon_timeout_raw"]
        mcp_search_call_timeout_raw = extended_raw_values["mcp_search_call_timeout_raw"]
        mcp_read_call_timeout_raw = extended_raw_values["mcp_read_call_timeout_raw"]
        strict_protocol_raw = extended_raw_values["strict_protocol_raw"]
        stabilization_enabled_raw = extended_raw_values["stabilization_enabled_raw"]
        http_bg_proxy_enabled_raw = extended_raw_values["http_bg_proxy_enabled_raw"]
        http_bg_proxy_target = extended_raw_values["http_bg_proxy_target"]
        parser = _ConfigValueParser()
        run_mode = "prod" if run_mode_raw == "prod" else "dev"
        retry_max = parser.int_min(retry_max_raw, minimum=1, default=5)
        backoff_sec = parser.int_min(backoff_raw, minimum=1, default=1)
        poll_ms = parser.int_min(poll_raw, minimum=100, default=500)
        debounce_ms = parser.int_min(debounce_raw, minimum=50, default=300)
        worker_count = parser.int_min(worker_raw, minimum=1, default=4)
        p95_threshold_ms = parser.int_min(p95_raw, minimum=1, default=180_000)
        dead_ratio_bps = parser.int_min(dead_ratio_raw, minimum=1, default=10)
        alert_window_sec = parser.int_min(alert_window_raw, minimum=60, default=300)
        auto_tick_sec = parser.int_min(auto_tick_raw, minimum=1, default=5)
        heartbeat_sec = parser.int_min(heartbeat_raw, minimum=1, default=2)
        stale_timeout_sec = parser.int_min(stale_timeout_raw, minimum=5, default=15)
        lsp_request_timeout_sec = parser.float_min(lsp_timeout_raw, minimum=0.1, default=20.0)
        lsp_max_instances_per_repo_language = parser.int_min(lsp_max_per_repo_lang_raw, minimum=1, default=3)
        lsp_global_soft_limit = parser.int_min(lsp_global_soft_limit_raw, minimum=0, default=0)
        lsp_bulk_max_instances_per_repo_language = parser.int_min(lsp_bulk_max_per_repo_lang_raw, minimum=1, default=4)
        lsp_interactive_reserved_slots_per_repo_language = parser.int_min(lsp_interactive_reserved_slots_raw, minimum=0, default=1)
        lsp_interactive_timeout_sec = parser.float_min(lsp_interactive_timeout_raw, minimum=0.1, default=4.0)
        lsp_interactive_queue_max = parser.int_min(lsp_interactive_queue_max_raw, minimum=16, default=256)
        lsp_symbol_info_budget_sec = parser.float_min(lsp_symbol_info_budget_raw, minimum=0.0, default=10.0)
        l3_executor_max_workers = parser.int_min(l3_executor_max_workers_raw, minimum=0, default=0)
        l3_recent_success_ttl_sec = parser.int_min(l3_recent_success_ttl_raw, minimum=0, default=120)
        l3_backpressure_cooldown_ms = parser.int_min(l3_backpressure_cooldown_ms_raw, minimum=10, default=300)
        lsp_scale_out_hot_hits = parser.int_min(lsp_scale_out_hot_hits_raw, minimum=2, default=24)
        lsp_file_buffer_idle_ttl_sec = parser.float_min(lsp_file_buffer_idle_ttl_raw, minimum=1.0, default=20.0)
        lsp_file_buffer_max_open = parser.int_min(lsp_file_buffer_max_open_raw, minimum=16, default=512)
        lsp_java_min_major = parser.int_min(lsp_java_min_major_raw, minimum=8, default=17)
        lsp_probe_timeout_default_sec = parser.float_min(lsp_probe_timeout_default_raw, minimum=0.1, default=20.0)
        lsp_probe_timeout_go_sec = parser.float_min(lsp_probe_timeout_go_raw, minimum=0.1, default=45.0)
        lsp_probe_workers = parser.int_min(lsp_probe_workers_raw, minimum=1, default=8)
        lsp_probe_l1_workers = parser.int_min(lsp_probe_l1_workers_raw, minimum=1, default=4)
        lsp_probe_force_join_ms = parser.int_min(lsp_probe_force_join_ms_raw, minimum=0, default=300)
        lsp_probe_warming_retry_sec = parser.int_min(lsp_probe_warming_retry_sec_raw, minimum=1, default=5)
        lsp_probe_warming_threshold = parser.int_min(lsp_probe_warming_threshold_raw, minimum=1, default=6)
        lsp_probe_permanent_backoff_sec = parser.int_min(lsp_probe_permanent_backoff_sec_raw, minimum=60, default=1800)
        lsp_probe_bootstrap_file_window = parser.int_min(lsp_probe_bootstrap_file_window_raw, minimum=1, default=256)
        lsp_probe_bootstrap_top_k = parser.int_min(lsp_probe_bootstrap_top_k_raw, minimum=1, default=3)
        lsp_probe_language_priority = _parse_csv_setting(lsp_probe_language_priority_raw, cls.lsp_probe_language_priority)
        lsp_probe_l1_languages = _parse_csv_setting(lsp_probe_l1_languages_raw, cls.lsp_probe_l1_languages)
        lsp_scope_java_markers = _parse_csv_setting(lsp_scope_java_markers_raw, cls.lsp_scope_java_markers)
        lsp_scope_ts_markers = _parse_csv_setting(lsp_scope_ts_markers_raw, cls.lsp_scope_ts_markers)
        lsp_scope_vue_markers = _parse_csv_setting(lsp_scope_vue_markers_raw, cls.lsp_scope_vue_markers)
        lsp_scope_active_languages = _parse_csv_setting(lsp_scope_active_languages_raw, cls.lsp_scope_active_languages)
        lsp_hotness_event_window_sec = parser.float_min(
            lsp_hotness_event_window_sec_raw,
            minimum=1.0,
            default=cls.lsp_hotness_event_window_sec,
        )
        lsp_hotness_decay_window_sec = parser.float_min(
            lsp_hotness_decay_window_sec_raw,
            minimum=lsp_hotness_event_window_sec,
            default=max(lsp_hotness_event_window_sec, cls.lsp_hotness_decay_window_sec),
        )
        lsp_broker_backlog_min_share = parser.float_range(
            lsp_broker_backlog_min_share_raw,
            minimum=0.0,
            maximum=1.0,
            default=cls.lsp_broker_backlog_min_share,
        )
        lsp_broker_max_standby_sessions_per_lang = parser.int_min(
            lsp_broker_max_standby_sessions_per_lang_raw,
            minimum=0,
            default=cls.lsp_broker_max_standby_sessions_per_lang,
        )
        lsp_broker_max_standby_sessions_per_budget_group = parser.int_min(
            lsp_broker_max_standby_sessions_per_budget_group_raw,
            minimum=0,
            default=cls.lsp_broker_max_standby_sessions_per_budget_group,
        )
        lsp_broker_ts_vue_active_cap = parser.int_min(
            lsp_broker_ts_vue_active_cap_raw,
            minimum=0,
            default=cls.lsp_broker_ts_vue_active_cap,
        )
        (
            lsp_broker_java_hot_lanes,
            lsp_broker_java_backlog_lanes,
            lsp_broker_java_sticky_ttl_sec,
            lsp_broker_java_switch_cooldown_sec,
            lsp_broker_java_min_lease_ms,
        ) = parser.parse_lane_bundle(
            hot_raw=lsp_broker_java_hot_lanes_raw,
            backlog_raw=lsp_broker_java_backlog_lanes_raw,
            sticky_raw=lsp_broker_java_sticky_ttl_sec_raw,
            switch_raw=lsp_broker_java_switch_cooldown_sec_raw,
            min_lease_raw=lsp_broker_java_min_lease_ms_raw,
            default=(
                cls.lsp_broker_java_hot_lanes,
                cls.lsp_broker_java_backlog_lanes,
                cls.lsp_broker_java_sticky_ttl_sec,
                cls.lsp_broker_java_switch_cooldown_sec,
                cls.lsp_broker_java_min_lease_ms,
            ),
        )
        (
            lsp_broker_ts_hot_lanes,
            lsp_broker_ts_backlog_lanes,
            lsp_broker_ts_sticky_ttl_sec,
            lsp_broker_ts_switch_cooldown_sec,
            lsp_broker_ts_min_lease_ms,
        ) = parser.parse_lane_bundle(
            hot_raw=lsp_broker_ts_hot_lanes_raw,
            backlog_raw=lsp_broker_ts_backlog_lanes_raw,
            sticky_raw=lsp_broker_ts_sticky_ttl_sec_raw,
            switch_raw=lsp_broker_ts_switch_cooldown_sec_raw,
            min_lease_raw=lsp_broker_ts_min_lease_ms_raw,
            default=(
                cls.lsp_broker_ts_hot_lanes,
                cls.lsp_broker_ts_backlog_lanes,
                cls.lsp_broker_ts_sticky_ttl_sec,
                cls.lsp_broker_ts_switch_cooldown_sec,
                cls.lsp_broker_ts_min_lease_ms,
            ),
        )
        (
            lsp_broker_vue_hot_lanes,
            lsp_broker_vue_backlog_lanes,
            lsp_broker_vue_sticky_ttl_sec,
            lsp_broker_vue_switch_cooldown_sec,
            lsp_broker_vue_min_lease_ms,
        ) = parser.parse_lane_bundle(
            hot_raw=lsp_broker_vue_hot_lanes_raw,
            backlog_raw=lsp_broker_vue_backlog_lanes_raw,
            sticky_raw=lsp_broker_vue_sticky_ttl_sec_raw,
            switch_raw=lsp_broker_vue_switch_cooldown_sec_raw,
            min_lease_raw=lsp_broker_vue_min_lease_ms_raw,
            default=(
                cls.lsp_broker_vue_hot_lanes,
                cls.lsp_broker_vue_backlog_lanes,
                cls.lsp_broker_vue_sticky_ttl_sec,
                cls.lsp_broker_vue_switch_cooldown_sec,
                cls.lsp_broker_vue_min_lease_ms,
            ),
        )
        lsp_broker_batch_throughput_pending_threshold = parser.int_min(
            lsp_broker_batch_throughput_pending_threshold_raw,
            minimum=1,
            default=cls.lsp_broker_batch_throughput_pending_threshold,
        )
        l3_supported_languages = _parse_csv_setting(l3_supported_languages_raw, cls.l3_supported_languages)
        lsp_max_concurrent_starts = parser.int_range(lsp_max_concurrent_starts_raw, minimum=1, maximum=4, default=4)
        lsp_max_concurrent_l1_probes = parser.int_range(lsp_max_concurrent_l1_probes_raw, minimum=1, maximum=8, default=4)
        orphan_check_sec = parser.int_min(orphan_check_raw, minimum=1, default=1)
        shutdown_join_sec = parser.int_min(shutdown_join_raw, minimum=1, default=2)
        vector_dim = parser.int_min(vector_dim_raw, minimum=16, default=128)
        vector_candidate_k = parser.int_min(vector_candidate_raw, minimum=1, default=50)
        vector_rerank_k = parser.int_min(vector_rerank_raw, minimum=1, default=20)
        vector_blend_weight = parser.float_range(vector_blend_raw, minimum=0.0, maximum=1.0, default=0.2)
        vector_min_similarity_threshold = parser.float_range(vector_min_similarity_raw, minimum=0.0, maximum=1.0, default=0.15)
        vector_max_boost = parser.float_range(vector_max_boost_raw, minimum=0.0, maximum=1.0, default=0.2)
        vector_min_token_count_for_rerank = parser.int_min(vector_min_token_raw, minimum=1, default=2)
        importance_max_boost = parser.float_min(importance_max_boost_raw, minimum=0.0, default=200.0)
        ranking_w_rrf = parser.float_range(ranking_w_rrf_raw, minimum=0.0, maximum=1.0, default=0.55)
        ranking_w_importance = parser.float_range(ranking_w_importance_raw, minimum=0.0, maximum=1.0, default=0.30)
        ranking_w_vector = parser.float_range(ranking_w_vector_raw, minimum=0.0, maximum=1.0, default=0.15)
        ranking_w_hierarchy = parser.float_range(ranking_w_hierarchy_raw, minimum=0.0, maximum=1.0, default=0.15)
        mcp_daemon_timeout_sec = parser.float_min(mcp_daemon_timeout_raw, minimum=0.1, default=2.0)
        mcp_search_call_timeout_sec = parser.float_min(mcp_search_call_timeout_raw, minimum=0.0, default=0.0)
        mcp_read_call_timeout_sec = parser.float_min(mcp_read_call_timeout_raw, minimum=0.0, default=0.0)
        total_weight = ranking_w_rrf + ranking_w_importance + ranking_w_vector + ranking_w_hierarchy
        if total_weight > 0.0:
            ranking_w_rrf = ranking_w_rrf / total_weight
            ranking_w_importance = ranking_w_importance / total_weight
            ranking_w_vector = ranking_w_vector / total_weight
            ranking_w_hierarchy = ranking_w_hierarchy / total_weight
        else:
            ranking_w_rrf = 0.55
            ranking_w_importance = 0.30
            ranking_w_vector = 0.15
            ranking_w_hierarchy = 0.15
        normalized_mode = importance_normalize_mode.lower()
        if normalized_mode not in {"none", "log1p", "minmax"}:
            normalized_mode = "log1p"
        search_lsp_fallback_mode = "strict" if search_lsp_fallback_mode_raw == "strict" else "normal"
        search_lsp_pressure_pending_threshold = parser.int_min(search_lsp_pressure_pending_threshold_raw, minimum=0, default=1)
        search_lsp_pressure_timeout_threshold = parser.int_min(search_lsp_pressure_timeout_threshold_raw, minimum=0, default=1)
        search_lsp_pressure_rejected_threshold = parser.int_min(search_lsp_pressure_rejected_threshold_raw, minimum=0, default=1)
        search_lsp_recent_failure_cooldown_sec = parser.float_min(search_lsp_recent_failure_cooldown_sec_raw, minimum=0.0, default=5.0)
        l5_call_rate_total_max = parser.float_range(l5_call_rate_total_max_raw, minimum=0.0, maximum=1.0, default=0.05)
        l5_call_rate_batch_max = parser.float_range(l5_call_rate_batch_max_raw, minimum=0.0, maximum=1.0, default=0.01)
        l5_calls_per_min_per_lang_max = parser.int_min(l5_calls_per_min_per_lang_max_raw, minimum=1, default=30)
        l5_tokens_per_10sec_global_max = parser.int_min(l5_tokens_per_10sec_global_max_raw, minimum=1, default=120)
        l5_tokens_per_10sec_per_lang_max = parser.int_min(l5_tokens_per_10sec_per_lang_max_raw, minimum=1, default=30)
        l5_tokens_per_10sec_per_workspace_max = parser.int_min(l5_tokens_per_10sec_per_workspace_max_raw, minimum=1, default=20)
        l3_query_compile_ms_budget = parser.float_min(l3_query_compile_ms_budget_raw, minimum=0.1, default=10.0)
        l3_query_budget_ms = parser.float_min(l3_query_budget_ms_raw, minimum=0.1, default=30.0)
        l3_tree_sitter_subinterp_workers = parser.int_min(
            l3_tree_sitter_subinterp_workers_raw,
            minimum=1,
            default=4,
        )
        l3_tree_sitter_subinterp_min_bytes = parser.int_min(
            l3_tree_sitter_subinterp_min_bytes_raw,
            minimum=0,
            default=4096,
        )
        l5_symbol_normalizer_subinterp_workers = parser.int_min(
            l5_symbol_normalizer_subinterp_workers_raw,
            minimum=1,
            default=2,
        )
        l5_symbol_normalizer_subinterp_min_symbols = parser.int_min(
            l5_symbol_normalizer_subinterp_min_symbols_raw,
            minimum=0,
            default=200,
        )
        include_ext_raw = os.getenv("SARI_COLLECTION_INCLUDE_EXT", "")
        exclude_globs_raw = os.getenv("SARI_COLLECTION_EXCLUDE_GLOBS", "")
        vector_apply_types_raw = os.getenv("SARI_VECTOR_APPLY_TO_ITEM_TYPES", "")
        importance_core_paths_raw = os.getenv("SARI_IMPORTANCE_CORE_PATH_TOKENS", "")
        importance_noisy_paths_raw = os.getenv("SARI_IMPORTANCE_NOISY_PATH_TOKENS", "")
        importance_code_ext_raw = os.getenv("SARI_IMPORTANCE_CODE_EXTENSIONS", "")
        importance_noisy_ext_raw = os.getenv("SARI_IMPORTANCE_NOISY_EXTENSIONS", "")
        include_ext = _parse_csv_setting(
            include_ext_raw,
            default_value=_read_tuple_setting(file_config, "collection_include_ext", cls.collection_include_ext),
        )
        l3_asset_lang_allowlist = _parse_csv_setting(
            l3_asset_lang_allowlist_raw,
            default_value=_read_tuple_setting(file_config, "l3_asset_lang_allowlist", cls.l3_asset_lang_allowlist),
        )
        exclude_globs = _parse_csv_setting(
            exclude_globs_raw,
            default_value=_read_tuple_setting(file_config, "collection_exclude_globs", cls.collection_exclude_globs),
        )
        vector_apply_to_item_types = _parse_csv_setting(
            vector_apply_types_raw,
            default_value=_read_tuple_setting(file_config, "vector_apply_to_item_types", cls.vector_apply_to_item_types),
        )
        importance_core_path_tokens = _parse_csv_setting(
            importance_core_paths_raw,
            default_value=_read_tuple_setting(file_config, "importance_core_path_tokens", cls.importance_core_path_tokens),
        )
        importance_noisy_path_tokens = _parse_csv_setting(
            importance_noisy_paths_raw,
            default_value=_read_tuple_setting(file_config, "importance_noisy_path_tokens", cls.importance_noisy_path_tokens),
        )
        importance_code_extensions = _parse_csv_setting(
            importance_code_ext_raw,
            default_value=_read_tuple_setting(file_config, "importance_code_extensions", cls.importance_code_extensions),
        )
        importance_noisy_extensions = _parse_csv_setting(
            importance_noisy_ext_raw,
            default_value=_read_tuple_setting(file_config, "importance_noisy_extensions", cls.importance_noisy_extensions),
        )
        collection_runtime = CollectionRuntimeConfigDTO(
            pipeline_retry_max=retry_max,
            pipeline_backoff_base_sec=backoff_sec,
            queue_poll_interval_ms=poll_ms,
            watcher_debounce_ms=debounce_ms,
            include_ext=include_ext,
            exclude_globs=exclude_globs,
        )
        lsp_hub_runtime = LspHubRuntimeConfigDTO(
            request_timeout_sec=lsp_request_timeout_sec,
            max_instances_per_repo_language=lsp_max_instances_per_repo_language,
            bulk_mode_enabled=parser.bool_true(lsp_bulk_mode_enabled_raw),
            bulk_max_instances_per_repo_language=lsp_bulk_max_instances_per_repo_language,
            interactive_reserved_slots_per_repo_language=lsp_interactive_reserved_slots_per_repo_language,
            interactive_timeout_sec=lsp_interactive_timeout_sec,
            lsp_global_soft_limit=lsp_global_soft_limit,
            scale_out_hot_hits=lsp_scale_out_hot_hits,
            file_buffer_idle_ttl_sec=lsp_file_buffer_idle_ttl_sec,
            file_buffer_max_open=lsp_file_buffer_max_open,
            java_min_major=lsp_java_min_major,
            max_concurrent_starts=lsp_max_concurrent_starts,
            max_concurrent_l1_probes=lsp_max_concurrent_l1_probes,
        )
        search_runtime = SearchRuntimeConfigDTO(
            candidate_backend=backend,
            candidate_fallback_scan=(fallback_flag != "0"),
            importance_kind_class=cls.importance_kind_class,
            importance_kind_function=cls.importance_kind_function,
            importance_kind_interface=cls.importance_kind_interface,
            importance_kind_method=cls.importance_kind_method,
            importance_fan_in_weight=cls.importance_fan_in_weight,
            importance_filename_exact_bonus=cls.importance_filename_exact_bonus,
            importance_core_path_bonus=cls.importance_core_path_bonus,
            importance_noisy_path_penalty=cls.importance_noisy_path_penalty,
            importance_code_ext_bonus=cls.importance_code_ext_bonus,
            importance_noisy_ext_penalty=cls.importance_noisy_ext_penalty,
            importance_recency_24h_multiplier=cls.importance_recency_24h_multiplier,
            importance_recency_7d_multiplier=cls.importance_recency_7d_multiplier,
            importance_recency_30d_multiplier=cls.importance_recency_30d_multiplier,
            importance_normalize_mode=normalized_mode,
            importance_max_boost=importance_max_boost,
            importance_core_path_tokens=importance_core_path_tokens,
            importance_noisy_path_tokens=importance_noisy_path_tokens,
            importance_code_extensions=importance_code_extensions,
            importance_noisy_extensions=importance_noisy_extensions,
            vector_enabled=parser.bool_true(vector_enabled_raw),
            vector_model_id=vector_model_id if vector_model_id != "" else "hashbow-v1",
            vector_dim=vector_dim,
            vector_candidate_k=vector_candidate_k,
            vector_rerank_k=vector_rerank_k,
            vector_blend_weight=vector_blend_weight,
            vector_min_similarity_threshold=vector_min_similarity_threshold,
            vector_max_boost=vector_max_boost,
            vector_min_token_count_for_rerank=vector_min_token_count_for_rerank,
            vector_apply_to_item_types=vector_apply_to_item_types,
            ranking_w_rrf=ranking_w_rrf,
            ranking_w_importance=ranking_w_importance,
            ranking_w_vector=ranking_w_vector,
            ranking_w_hierarchy=ranking_w_hierarchy,
            search_lsp_fallback_mode=search_lsp_fallback_mode,
            search_lsp_pressure_guard_enabled=parser.bool_true(search_lsp_pressure_guard_enabled_raw),
            search_lsp_pressure_pending_threshold=search_lsp_pressure_pending_threshold,
            search_lsp_pressure_timeout_threshold=search_lsp_pressure_timeout_threshold,
            search_lsp_pressure_rejected_threshold=search_lsp_pressure_rejected_threshold,
            search_lsp_recent_failure_cooldown_sec=search_lsp_recent_failure_cooldown_sec,
            lsp_include_info_default=parser.bool_true(lsp_include_info_default_raw),
            lsp_symbol_info_budget_sec=lsp_symbol_info_budget_sec,
        )
        return cls(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=50,
            stop_grace_sec=10,
            candidate_backend=search_runtime.candidate_backend,
            candidate_fallback_scan=search_runtime.candidate_fallback_scan,
            pipeline_retry_max=collection_runtime.pipeline_retry_max,
            pipeline_backoff_base_sec=collection_runtime.pipeline_backoff_base_sec,
            queue_poll_interval_ms=collection_runtime.queue_poll_interval_ms,
            watcher_debounce_ms=collection_runtime.watcher_debounce_ms,
            collection_include_ext=collection_runtime.include_ext,
            collection_exclude_globs=collection_runtime.exclude_globs,
            pipeline_worker_count=worker_count,
            pipeline_l3_p95_threshold_ms=p95_threshold_ms,
            pipeline_dead_ratio_threshold_bps=dead_ratio_bps,
            pipeline_alert_window_sec=alert_window_sec,
            pipeline_auto_tick_interval_sec=auto_tick_sec,
            l3_parallel_enabled=parser.bool_true(l3_parallel_enabled_raw),
            run_mode=run_mode,
            daemon_heartbeat_interval_sec=heartbeat_sec,
            daemon_stale_timeout_sec=stale_timeout_sec,
            lsp_request_timeout_sec=lsp_hub_runtime.request_timeout_sec,
            lsp_max_instances_per_repo_language=lsp_hub_runtime.max_instances_per_repo_language,
            lsp_bulk_mode_enabled=lsp_hub_runtime.bulk_mode_enabled,
            lsp_bulk_max_instances_per_repo_language=lsp_hub_runtime.bulk_max_instances_per_repo_language,
            lsp_interactive_reserved_slots_per_repo_language=lsp_hub_runtime.interactive_reserved_slots_per_repo_language,
            lsp_interactive_timeout_sec=lsp_hub_runtime.interactive_timeout_sec,
            lsp_interactive_queue_max=lsp_interactive_queue_max,
            lsp_symbol_info_budget_sec=lsp_symbol_info_budget_sec,
            lsp_include_info_default=search_runtime.lsp_include_info_default,
            lsp_global_soft_limit=lsp_hub_runtime.lsp_global_soft_limit,
            lsp_scale_out_hot_hits=lsp_hub_runtime.scale_out_hot_hits,
            l3_executor_max_workers=l3_executor_max_workers,
            l3_recent_success_ttl_sec=l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=parser.bool_true(l3_backpressure_on_interactive_raw),
            l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms,
            l3_supported_languages=l3_supported_languages,
            lsp_file_buffer_idle_ttl_sec=lsp_hub_runtime.file_buffer_idle_ttl_sec,
            lsp_file_buffer_max_open=lsp_hub_runtime.file_buffer_max_open,
            lsp_java_min_major=lsp_hub_runtime.java_min_major,
            lsp_probe_timeout_default_sec=lsp_probe_timeout_default_sec,
            lsp_probe_timeout_go_sec=lsp_probe_timeout_go_sec,
            lsp_probe_workers=lsp_probe_workers,
            lsp_probe_l1_workers=lsp_probe_l1_workers,
            lsp_probe_force_join_ms=lsp_probe_force_join_ms,
            lsp_probe_warming_retry_sec=lsp_probe_warming_retry_sec,
            lsp_probe_warming_threshold=lsp_probe_warming_threshold,
            lsp_probe_permanent_backoff_sec=lsp_probe_permanent_backoff_sec,
            lsp_probe_bootstrap_file_window=lsp_probe_bootstrap_file_window,
            lsp_probe_bootstrap_top_k=lsp_probe_bootstrap_top_k,
            lsp_probe_language_priority=lsp_probe_language_priority,
            lsp_probe_l1_languages=lsp_probe_l1_languages,
            lsp_scope_planner_enabled=parser.bool_true(lsp_scope_planner_enabled_raw),
            lsp_scope_planner_shadow_mode=parser.bool_true(lsp_scope_planner_shadow_mode_raw),
            lsp_scope_java_markers=lsp_scope_java_markers,
            lsp_scope_ts_markers=lsp_scope_ts_markers,
            lsp_scope_vue_markers=lsp_scope_vue_markers,
            lsp_scope_top_level_fallback=parser.bool_true(lsp_scope_top_level_fallback_raw),
            lsp_scope_active_languages=lsp_scope_active_languages,
            lsp_session_broker_enabled=parser.bool_true(lsp_session_broker_enabled_raw),
            lsp_session_broker_metrics_enabled=parser.bool_true(lsp_session_broker_metrics_enabled_raw),
            lsp_broker_optional_scaffolding_enabled=parser.bool_true(lsp_broker_optional_scaffolding_enabled_raw),
            lsp_broker_batch_throughput_mode_enabled=parser.bool_true(lsp_broker_batch_throughput_mode_enabled_raw),
            lsp_broker_batch_throughput_pending_threshold=lsp_broker_batch_throughput_pending_threshold,
            lsp_broker_batch_disable_java_probe=parser.bool_true(lsp_broker_batch_disable_java_probe_raw),
            lsp_hotness_event_window_sec=lsp_hotness_event_window_sec,
            lsp_hotness_decay_window_sec=lsp_hotness_decay_window_sec,
            lsp_broker_backlog_min_share=lsp_broker_backlog_min_share,
            lsp_broker_max_standby_sessions_per_lang=lsp_broker_max_standby_sessions_per_lang,
            lsp_broker_max_standby_sessions_per_budget_group=lsp_broker_max_standby_sessions_per_budget_group,
            lsp_broker_ts_vue_active_cap=lsp_broker_ts_vue_active_cap,
            lsp_broker_java_hot_lanes=lsp_broker_java_hot_lanes,
            lsp_broker_java_backlog_lanes=lsp_broker_java_backlog_lanes,
            lsp_broker_java_sticky_ttl_sec=lsp_broker_java_sticky_ttl_sec,
            lsp_broker_java_switch_cooldown_sec=lsp_broker_java_switch_cooldown_sec,
            lsp_broker_java_min_lease_ms=lsp_broker_java_min_lease_ms,
            lsp_broker_ts_hot_lanes=lsp_broker_ts_hot_lanes,
            lsp_broker_ts_backlog_lanes=lsp_broker_ts_backlog_lanes,
            lsp_broker_ts_sticky_ttl_sec=lsp_broker_ts_sticky_ttl_sec,
            lsp_broker_ts_switch_cooldown_sec=lsp_broker_ts_switch_cooldown_sec,
            lsp_broker_ts_min_lease_ms=lsp_broker_ts_min_lease_ms,
            lsp_broker_vue_hot_lanes=lsp_broker_vue_hot_lanes,
            lsp_broker_vue_backlog_lanes=lsp_broker_vue_backlog_lanes,
            lsp_broker_vue_sticky_ttl_sec=lsp_broker_vue_sticky_ttl_sec,
            lsp_broker_vue_switch_cooldown_sec=lsp_broker_vue_switch_cooldown_sec,
            lsp_broker_vue_min_lease_ms=lsp_broker_vue_min_lease_ms,
            lsp_max_concurrent_starts=lsp_hub_runtime.max_concurrent_starts,
            lsp_max_concurrent_l1_probes=lsp_hub_runtime.max_concurrent_l1_probes,
            orphan_ppid_check_interval_sec=orphan_check_sec,
            shutdown_join_timeout_sec=shutdown_join_sec,
            importance_normalize_mode=search_runtime.importance_normalize_mode,
            importance_max_boost=search_runtime.importance_max_boost,
            importance_core_path_tokens=search_runtime.importance_core_path_tokens,
            importance_noisy_path_tokens=search_runtime.importance_noisy_path_tokens,
            importance_code_extensions=search_runtime.importance_code_extensions,
            importance_noisy_extensions=search_runtime.importance_noisy_extensions,
            vector_enabled=search_runtime.vector_enabled,
            vector_model_id=search_runtime.vector_model_id,
            vector_dim=search_runtime.vector_dim,
            vector_candidate_k=search_runtime.vector_candidate_k,
            vector_rerank_k=search_runtime.vector_rerank_k,
            vector_blend_weight=search_runtime.vector_blend_weight,
            vector_min_similarity_threshold=search_runtime.vector_min_similarity_threshold,
            vector_max_boost=search_runtime.vector_max_boost,
            vector_min_token_count_for_rerank=search_runtime.vector_min_token_count_for_rerank,
            vector_apply_to_item_types=search_runtime.vector_apply_to_item_types,
            ranking_w_rrf=search_runtime.ranking_w_rrf,
            ranking_w_importance=search_runtime.ranking_w_importance,
            ranking_w_vector=search_runtime.ranking_w_vector,
            ranking_w_hierarchy=search_runtime.ranking_w_hierarchy,
            search_lsp_fallback_mode=search_runtime.search_lsp_fallback_mode,
            search_lsp_pressure_guard_enabled=search_runtime.search_lsp_pressure_guard_enabled,
            search_lsp_pressure_pending_threshold=search_runtime.search_lsp_pressure_pending_threshold,
            search_lsp_pressure_timeout_threshold=search_runtime.search_lsp_pressure_timeout_threshold,
            search_lsp_pressure_rejected_threshold=search_runtime.search_lsp_pressure_rejected_threshold,
            search_lsp_recent_failure_cooldown_sec=search_runtime.search_lsp_recent_failure_cooldown_sec,
            l5_call_rate_total_max=l5_call_rate_total_max,
            l5_call_rate_batch_max=l5_call_rate_batch_max,
            l5_calls_per_min_per_lang_max=l5_calls_per_min_per_lang_max,
            l5_tokens_per_10sec_global_max=l5_tokens_per_10sec_global_max,
            l5_tokens_per_10sec_per_lang_max=l5_tokens_per_10sec_per_lang_max,
            l5_tokens_per_10sec_per_workspace_max=l5_tokens_per_10sec_per_workspace_max,
            l3_query_compile_cache_enabled=parser.bool_true(l3_query_compile_cache_enabled_raw),
            l3_query_compile_ms_budget=l3_query_compile_ms_budget,
            l3_query_budget_ms=l3_query_budget_ms,
            l3_tree_sitter_executor_mode=(
                l3_tree_sitter_executor_mode_raw
                if l3_tree_sitter_executor_mode_raw in {"inline", "subinterp"}
                else "inline"
            ),
            l3_tree_sitter_subinterp_workers=l3_tree_sitter_subinterp_workers,
            l3_tree_sitter_subinterp_min_bytes=l3_tree_sitter_subinterp_min_bytes,
            l3_asset_mode=(
                l3_asset_mode_raw
                if l3_asset_mode_raw in {"shadow", "gate", "apply"}
                else "shadow"
            ),
            l3_asset_manifest_path=l3_asset_manifest_path,
            l3_asset_lang_allowlist=l3_asset_lang_allowlist,
            l5_db_short_circuit_enabled=parser.bool_true(l5_db_short_circuit_enabled_raw),
            l5_db_short_circuit_log_miss_reason=parser.bool_true(l5_db_short_circuit_log_miss_reason_raw),
            l5_symbol_normalizer_executor_mode=(
                l5_symbol_normalizer_executor_mode_raw
                if l5_symbol_normalizer_executor_mode_raw in {"inline", "subinterp"}
                else "inline"
            ),
            l5_symbol_normalizer_subinterp_workers=l5_symbol_normalizer_subinterp_workers,
            l5_symbol_normalizer_subinterp_min_symbols=l5_symbol_normalizer_subinterp_min_symbols,
            mcp_forward_to_daemon=parser.bool_true(mcp_forward_to_daemon_raw),
            mcp_daemon_autostart=parser.bool_true(mcp_daemon_autostart_raw),
            mcp_daemon_timeout_sec=mcp_daemon_timeout_sec,
            mcp_search_call_timeout_sec=mcp_search_call_timeout_sec,
            mcp_read_call_timeout_sec=mcp_read_call_timeout_sec,
            strict_protocol=parser.bool_true(strict_protocol_raw),
            stabilization_enabled=parser.bool_enabled(stabilization_enabled_raw),
            http_bg_proxy_enabled=parser.bool_true(http_bg_proxy_enabled_raw),
            http_bg_proxy_target=http_bg_proxy_target,
        )


def _load_user_config() -> dict[str, object]:
    """사용자 설정 파일을 읽어 딕셔너리로 반환한다."""
    config_path = Path.home() / ".sari" / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        logging.getLogger(__name__).warning("사용자 설정 파일을 읽는 데 실패했습니다(path=%s): %s", config_path, exc)
        return {}
    except ValueError as exc:
        logging.getLogger(__name__).warning("사용자 설정 파일의 JSON이 잘못되었습니다(path=%s): %s", config_path, exc)
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _read_tuple_setting(file_config: dict[str, object], key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """설정 딕셔너리의 문자열 배열 값을 튜플로 파싱한다."""
    raw_value = file_config.get(key)
    if not isinstance(raw_value, list):
        return fallback
    parsed: list[str] = []
    for item in raw_value:
        if isinstance(item, str) and item.strip() != "":
            parsed.append(item.strip())
    if len(parsed) == 0:
        return fallback
    return tuple(parsed)


def _parse_csv_setting(raw_value: str, default_value: tuple[str, ...]) -> tuple[str, ...]:
    """콤마 구분 환경변수를 튜플 설정으로 파싱한다."""
    if raw_value.strip() == "":
        return default_value
    parsed = [part.strip() for part in raw_value.split(",") if part.strip() != ""]
    if len(parsed) == 0:
        return default_value
    return tuple(parsed)


@dataclass(frozen=True)
class _ConfigField:
    """환경변수/파일 키를 묶어 raw 문자열 값을 읽기 위한 선언형 필드다."""

    name: str
    env_key: str
    file_key: str
    default: object
    lower: bool = False


def _read_config_fields(*, file_config: dict[str, object], fields: tuple[_ConfigField, ...]) -> dict[str, str]:
    """선언된 필드 목록을 일괄 로드해 raw 문자열 맵으로 반환한다."""
    resolved: dict[str, str] = {}
    for field in fields:
        raw = os.getenv(field.env_key, str(file_config.get(field.file_key, field.default))).strip()
        resolved[field.name] = raw.lower() if field.lower else raw
    return resolved


def _build_early_fields() -> tuple[_ConfigField, ...]:
    """default() 초기 부트스트랩 필드 목록."""
    return (
        _ConfigField("db_path_raw", "SARI_DB_PATH", "db_path", ""),
        _ConfigField("backend_raw", "SARI_CANDIDATE_BACKEND", "candidate_backend", "tantivy", lower=True),
        _ConfigField("fallback_flag", "SARI_CANDIDATE_FALLBACK_SCAN", "candidate_fallback_scan", 1),
    )


def _build_core_fields(*, file_config: dict[str, object], defaults: type[AppConfig]) -> tuple[_ConfigField, ...]:
    """pipeline/L3/LSP 핵심 설정 필드 목록."""
    return (
        _ConfigField("retry_max_raw", "SARI_PIPELINE_RETRY_MAX", "pipeline_retry_max", 5),
        _ConfigField("backoff_raw", "SARI_PIPELINE_BACKOFF_BASE_SEC", "pipeline_backoff_base_sec", 1),
        _ConfigField("poll_raw", "SARI_QUEUE_POLL_INTERVAL_MS", "queue_poll_interval_ms", 500),
        _ConfigField("debounce_raw", "SARI_WATCHER_DEBOUNCE_MS", "watcher_debounce_ms", 300),
        _ConfigField("worker_raw", "SARI_PIPELINE_WORKER_COUNT", "pipeline_worker_count", 4),
        _ConfigField("p95_raw", "SARI_PIPELINE_L3_P95_THRESHOLD_MS", "pipeline_l3_p95_threshold_ms", 180000),
        _ConfigField("dead_ratio_raw", "SARI_PIPELINE_DEAD_RATIO_THRESHOLD_BPS", "pipeline_dead_ratio_threshold_bps", 10),
        _ConfigField("alert_window_raw", "SARI_PIPELINE_ALERT_WINDOW_SEC", "pipeline_alert_window_sec", 300),
        _ConfigField("auto_tick_raw", "SARI_PIPELINE_AUTO_TICK_INTERVAL_SEC", "pipeline_auto_tick_interval_sec", 5),
        _ConfigField("l3_parallel_enabled_raw", "SARI_L3_PARALLEL_ENABLED", "l3_parallel_enabled", True, lower=True),
        _ConfigField("run_mode_raw", "SARI_RUN_MODE", "run_mode", "prod", lower=True),
        _ConfigField("heartbeat_raw", "SARI_DAEMON_HEARTBEAT_INTERVAL_SEC", "daemon_heartbeat_interval_sec", 2),
        _ConfigField("stale_timeout_raw", "SARI_DAEMON_STALE_TIMEOUT_SEC", "daemon_stale_timeout_sec", 15),
        _ConfigField("lsp_timeout_raw", "SARI_LSP_REQUEST_TIMEOUT_SEC", "lsp_request_timeout_sec", 20.0),
        _ConfigField("lsp_max_per_repo_lang_raw", "SARI_LSP_MAX_INSTANCES_PER_REPO_LANGUAGE", "lsp_max_instances_per_repo_language", 3),
        _ConfigField("lsp_bulk_mode_enabled_raw", "SARI_LSP_BULK_MODE_ENABLED", "lsp_bulk_mode_enabled", True, lower=True),
        _ConfigField("lsp_bulk_max_per_repo_lang_raw", "SARI_LSP_BULK_MAX_INSTANCES_PER_REPO_LANGUAGE", "lsp_bulk_max_instances_per_repo_language", 4),
        _ConfigField("lsp_interactive_reserved_slots_raw", "SARI_LSP_INTERACTIVE_RESERVED_SLOTS_PER_REPO_LANGUAGE", "lsp_interactive_reserved_slots_per_repo_language", 1),
        _ConfigField("lsp_interactive_timeout_raw", "SARI_LSP_INTERACTIVE_TIMEOUT_SEC", "lsp_interactive_timeout_sec", 4.0),
        _ConfigField("lsp_interactive_queue_max_raw", "SARI_LSP_INTERACTIVE_QUEUE_MAX", "lsp_interactive_queue_max", 256),
        _ConfigField("lsp_symbol_info_budget_raw", "SARI_LSP_SYMBOL_INFO_BUDGET_SEC", "lsp_symbol_info_budget_sec", 10.0),
        _ConfigField("lsp_include_info_default_raw", "SARI_LSP_INCLUDE_INFO_DEFAULT", "lsp_include_info_default", False, lower=True),
        _ConfigField("lsp_global_soft_limit_raw", "SARI_LSP_GLOBAL_SOFT_LIMIT", "lsp_global_soft_limit", 0),
        _ConfigField("l3_executor_max_workers_raw", "SARI_L3_EXECUTOR_MAX_WORKERS", "l3_executor_max_workers", 0),
        _ConfigField("l3_recent_success_ttl_raw", "SARI_L3_RECENT_SUCCESS_TTL_SEC", "l3_recent_success_ttl_sec", 120),
        _ConfigField("l3_backpressure_on_interactive_raw", "SARI_L3_BACKPRESSURE_ON_INTERACTIVE", "l3_backpressure_on_interactive", True, lower=True),
        _ConfigField("l3_backpressure_cooldown_ms_raw", "SARI_L3_BACKPRESSURE_COOLDOWN_MS", "l3_backpressure_cooldown_ms", 300),
        _ConfigField(
            "l3_supported_languages_raw",
            "SARI_L3_SUPPORTED_LANGUAGES",
            "l3_supported_languages",
            ",".join(_read_tuple_setting(file_config, "l3_supported_languages", defaults.l3_supported_languages)),
        ),
        _ConfigField("lsp_scale_out_hot_hits_raw", "SARI_LSP_SCALE_OUT_HOT_HITS", "lsp_scale_out_hot_hits", 24),
    )


def _build_extended_fields(*, file_config: dict[str, object], defaults: type[AppConfig]) -> tuple[_ConfigField, ...]:
    """broker/vector/search/L5/MCP 확장 설정 필드 목록."""
    return (
        _ConfigField("lsp_file_buffer_idle_ttl_raw", "SARI_LSP_FILE_BUFFER_IDLE_TTL_SEC", "lsp_file_buffer_idle_ttl_sec", 20.0),
        _ConfigField("lsp_file_buffer_max_open_raw", "SARI_LSP_FILE_BUFFER_MAX_OPEN", "lsp_file_buffer_max_open", 512),
        _ConfigField("lsp_java_min_major_raw", "SARI_LSP_JAVA_MIN_MAJOR", "lsp_java_min_major", 17),
        _ConfigField("lsp_probe_timeout_default_raw", "SARI_LSP_PROBE_TIMEOUT_DEFAULT_SEC", "lsp_probe_timeout_default_sec", 20.0),
        _ConfigField("lsp_probe_timeout_go_raw", "SARI_LSP_PROBE_TIMEOUT_GO_SEC", "lsp_probe_timeout_go_sec", 45.0),
        _ConfigField("lsp_probe_workers_raw", "SARI_LSP_PROBE_WORKERS", "lsp_probe_workers", 8),
        _ConfigField("lsp_probe_l1_workers_raw", "SARI_LSP_PROBE_L1_WORKERS", "lsp_probe_l1_workers", 4),
        _ConfigField("lsp_probe_force_join_ms_raw", "SARI_LSP_PROBE_FORCE_JOIN_MS", "lsp_probe_force_join_ms", 300),
        _ConfigField("lsp_probe_warming_retry_sec_raw", "SARI_LSP_PROBE_WARMING_RETRY_SEC", "lsp_probe_warming_retry_sec", 5),
        _ConfigField("lsp_probe_warming_threshold_raw", "SARI_LSP_PROBE_WARMING_THRESHOLD", "lsp_probe_warming_threshold", 6),
        _ConfigField("lsp_probe_permanent_backoff_sec_raw", "SARI_LSP_PROBE_PERMANENT_BACKOFF_SEC", "lsp_probe_permanent_backoff_sec", 1800),
        _ConfigField("lsp_probe_bootstrap_file_window_raw", "SARI_LSP_PROBE_BOOTSTRAP_FILE_WINDOW", "lsp_probe_bootstrap_file_window", 256),
        _ConfigField("lsp_probe_bootstrap_top_k_raw", "SARI_LSP_PROBE_BOOTSTRAP_TOP_K", "lsp_probe_bootstrap_top_k", 3),
        _ConfigField(
            "lsp_probe_language_priority_raw",
            "SARI_LSP_PROBE_LANGUAGE_PRIORITY",
            "lsp_probe_language_priority",
            ",".join(_read_tuple_setting(file_config, "lsp_probe_language_priority", defaults.lsp_probe_language_priority)),
        ),
        _ConfigField(
            "lsp_probe_l1_languages_raw",
            "SARI_LSP_PROBE_L1_LANGUAGES",
            "lsp_probe_l1_languages",
            ",".join(_read_tuple_setting(file_config, "lsp_probe_l1_languages", defaults.lsp_probe_l1_languages)),
        ),
        _ConfigField("lsp_scope_planner_enabled_raw", "SARI_LSP_SCOPE_PLANNER_ENABLED", "lsp_scope_planner_enabled", defaults.lsp_scope_planner_enabled, lower=True),
        _ConfigField("lsp_scope_planner_shadow_mode_raw", "SARI_LSP_SCOPE_PLANNER_SHADOW_MODE", "lsp_scope_planner_shadow_mode", defaults.lsp_scope_planner_shadow_mode, lower=True),
        _ConfigField(
            "lsp_scope_java_markers_raw",
            "SARI_LSP_SCOPE_JAVA_MARKERS",
            "lsp_scope_java_markers",
            ",".join(_read_tuple_setting(file_config, "lsp_scope_java_markers", defaults.lsp_scope_java_markers)),
        ),
        _ConfigField(
            "lsp_scope_ts_markers_raw",
            "SARI_LSP_SCOPE_TS_MARKERS",
            "lsp_scope_ts_markers",
            ",".join(_read_tuple_setting(file_config, "lsp_scope_ts_markers", defaults.lsp_scope_ts_markers)),
        ),
        _ConfigField(
            "lsp_scope_vue_markers_raw",
            "SARI_LSP_SCOPE_VUE_MARKERS",
            "lsp_scope_vue_markers",
            ",".join(_read_tuple_setting(file_config, "lsp_scope_vue_markers", defaults.lsp_scope_vue_markers)),
        ),
        _ConfigField("lsp_scope_top_level_fallback_raw", "SARI_LSP_SCOPE_TOP_LEVEL_FALLBACK", "lsp_scope_top_level_fallback", defaults.lsp_scope_top_level_fallback, lower=True),
        _ConfigField(
            "lsp_scope_active_languages_raw",
            "SARI_LSP_SCOPE_ACTIVE_LANGUAGES",
            "lsp_scope_active_languages",
            ",".join(_read_tuple_setting(file_config, "lsp_scope_active_languages", defaults.lsp_scope_active_languages)),
        ),
        _ConfigField("lsp_session_broker_enabled_raw", "SARI_LSP_SESSION_BROKER_ENABLED", "lsp_session_broker_enabled", defaults.lsp_session_broker_enabled, lower=True),
        _ConfigField("lsp_session_broker_metrics_enabled_raw", "SARI_LSP_SESSION_BROKER_METRICS_ENABLED", "lsp_session_broker_metrics_enabled", defaults.lsp_session_broker_metrics_enabled, lower=True),
        _ConfigField("lsp_broker_optional_scaffolding_enabled_raw", "SARI_LSP_BROKER_OPTIONAL_SCAFFOLDING_ENABLED", "lsp_broker_optional_scaffolding_enabled", defaults.lsp_broker_optional_scaffolding_enabled, lower=True),
        _ConfigField("lsp_broker_batch_throughput_mode_enabled_raw", "SARI_LSP_BROKER_BATCH_THROUGHPUT_MODE_ENABLED", "lsp_broker_batch_throughput_mode_enabled", defaults.lsp_broker_batch_throughput_mode_enabled, lower=True),
        _ConfigField(
            "lsp_broker_batch_throughput_pending_threshold_raw",
            "SARI_LSP_BROKER_BATCH_THROUGHPUT_PENDING_THRESHOLD",
            "lsp_broker_batch_throughput_pending_threshold",
            defaults.lsp_broker_batch_throughput_pending_threshold,
        ),
        _ConfigField("lsp_broker_batch_disable_java_probe_raw", "SARI_LSP_BROKER_BATCH_DISABLE_JAVA_PROBE", "lsp_broker_batch_disable_java_probe", defaults.lsp_broker_batch_disable_java_probe, lower=True),
        _ConfigField("lsp_hotness_event_window_sec_raw", "SARI_LSP_HOTNESS_EVENT_WINDOW_SEC", "lsp_hotness_event_window_sec", defaults.lsp_hotness_event_window_sec),
        _ConfigField("lsp_hotness_decay_window_sec_raw", "SARI_LSP_HOTNESS_DECAY_WINDOW_SEC", "lsp_hotness_decay_window_sec", defaults.lsp_hotness_decay_window_sec),
        _ConfigField("lsp_broker_backlog_min_share_raw", "SARI_LSP_BROKER_BACKLOG_MIN_SHARE", "lsp_broker_backlog_min_share", defaults.lsp_broker_backlog_min_share),
        _ConfigField("lsp_broker_max_standby_sessions_per_lang_raw", "SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_LANG", "lsp_broker_max_standby_sessions_per_lang", defaults.lsp_broker_max_standby_sessions_per_lang),
        _ConfigField("lsp_broker_max_standby_sessions_per_budget_group_raw", "SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_BUDGET_GROUP", "lsp_broker_max_standby_sessions_per_budget_group", defaults.lsp_broker_max_standby_sessions_per_budget_group),
        _ConfigField("lsp_broker_ts_vue_active_cap_raw", "SARI_LSP_BROKER_TS_VUE_ACTIVE_CAP", "lsp_broker_ts_vue_active_cap", defaults.lsp_broker_ts_vue_active_cap),
        _ConfigField("lsp_broker_java_hot_lanes_raw", "SARI_LSP_BROKER_JAVA_HOT_LANES", "lsp_broker_java_hot_lanes", defaults.lsp_broker_java_hot_lanes),
        _ConfigField("lsp_broker_java_backlog_lanes_raw", "SARI_LSP_BROKER_JAVA_BACKLOG_LANES", "lsp_broker_java_backlog_lanes", defaults.lsp_broker_java_backlog_lanes),
        _ConfigField("lsp_broker_java_sticky_ttl_sec_raw", "SARI_LSP_BROKER_JAVA_STICKY_TTL_SEC", "lsp_broker_java_sticky_ttl_sec", defaults.lsp_broker_java_sticky_ttl_sec),
        _ConfigField("lsp_broker_java_switch_cooldown_sec_raw", "SARI_LSP_BROKER_JAVA_SWITCH_COOLDOWN_SEC", "lsp_broker_java_switch_cooldown_sec", defaults.lsp_broker_java_switch_cooldown_sec),
        _ConfigField("lsp_broker_java_min_lease_ms_raw", "SARI_LSP_BROKER_JAVA_MIN_LEASE_MS", "lsp_broker_java_min_lease_ms", defaults.lsp_broker_java_min_lease_ms),
        _ConfigField("lsp_broker_ts_hot_lanes_raw", "SARI_LSP_BROKER_TS_HOT_LANES", "lsp_broker_ts_hot_lanes", defaults.lsp_broker_ts_hot_lanes),
        _ConfigField("lsp_broker_ts_backlog_lanes_raw", "SARI_LSP_BROKER_TS_BACKLOG_LANES", "lsp_broker_ts_backlog_lanes", defaults.lsp_broker_ts_backlog_lanes),
        _ConfigField("lsp_broker_ts_sticky_ttl_sec_raw", "SARI_LSP_BROKER_TS_STICKY_TTL_SEC", "lsp_broker_ts_sticky_ttl_sec", defaults.lsp_broker_ts_sticky_ttl_sec),
        _ConfigField("lsp_broker_ts_switch_cooldown_sec_raw", "SARI_LSP_BROKER_TS_SWITCH_COOLDOWN_SEC", "lsp_broker_ts_switch_cooldown_sec", defaults.lsp_broker_ts_switch_cooldown_sec),
        _ConfigField("lsp_broker_ts_min_lease_ms_raw", "SARI_LSP_BROKER_TS_MIN_LEASE_MS", "lsp_broker_ts_min_lease_ms", defaults.lsp_broker_ts_min_lease_ms),
        _ConfigField("lsp_broker_vue_hot_lanes_raw", "SARI_LSP_BROKER_VUE_HOT_LANES", "lsp_broker_vue_hot_lanes", defaults.lsp_broker_vue_hot_lanes),
        _ConfigField("lsp_broker_vue_backlog_lanes_raw", "SARI_LSP_BROKER_VUE_BACKLOG_LANES", "lsp_broker_vue_backlog_lanes", defaults.lsp_broker_vue_backlog_lanes),
        _ConfigField("lsp_broker_vue_sticky_ttl_sec_raw", "SARI_LSP_BROKER_VUE_STICKY_TTL_SEC", "lsp_broker_vue_sticky_ttl_sec", defaults.lsp_broker_vue_sticky_ttl_sec),
        _ConfigField("lsp_broker_vue_switch_cooldown_sec_raw", "SARI_LSP_BROKER_VUE_SWITCH_COOLDOWN_SEC", "lsp_broker_vue_switch_cooldown_sec", defaults.lsp_broker_vue_switch_cooldown_sec),
        _ConfigField("lsp_broker_vue_min_lease_ms_raw", "SARI_LSP_BROKER_VUE_MIN_LEASE_MS", "lsp_broker_vue_min_lease_ms", defaults.lsp_broker_vue_min_lease_ms),
        _ConfigField("lsp_max_concurrent_starts_raw", "SARI_LSP_MAX_CONCURRENT_STARTS", "lsp_max_concurrent_starts", 4),
        _ConfigField("lsp_max_concurrent_l1_probes_raw", "SARI_LSP_MAX_CONCURRENT_L1_PROBES", "lsp_max_concurrent_l1_probes", 4),
        _ConfigField("orphan_check_raw", "SARI_ORPHAN_PPID_CHECK_INTERVAL_SEC", "orphan_ppid_check_interval_sec", 1),
        _ConfigField("shutdown_join_raw", "SARI_SHUTDOWN_JOIN_TIMEOUT_SEC", "shutdown_join_timeout_sec", 2),
        _ConfigField("vector_enabled_raw", "SARI_VECTOR_ENABLED", "vector_enabled", False, lower=True),
        _ConfigField("vector_dim_raw", "SARI_VECTOR_DIM", "vector_dim", 128),
        _ConfigField("vector_candidate_raw", "SARI_VECTOR_CANDIDATE_K", "vector_candidate_k", 50),
        _ConfigField("vector_rerank_raw", "SARI_VECTOR_RERANK_K", "vector_rerank_k", 20),
        _ConfigField("vector_blend_raw", "SARI_VECTOR_BLEND_WEIGHT", "vector_blend_weight", 0.2),
        _ConfigField("vector_min_similarity_raw", "SARI_VECTOR_MIN_SIMILARITY_THRESHOLD", "vector_min_similarity_threshold", 0.15),
        _ConfigField("vector_max_boost_raw", "SARI_VECTOR_MAX_BOOST", "vector_max_boost", 0.2),
        _ConfigField("vector_min_token_raw", "SARI_VECTOR_MIN_TOKEN_COUNT_FOR_RERANK", "vector_min_token_count_for_rerank", 2),
        _ConfigField("importance_normalize_mode", "SARI_IMPORTANCE_NORMALIZE_MODE", "importance_normalize_mode", "log1p"),
        _ConfigField("importance_max_boost_raw", "SARI_IMPORTANCE_MAX_BOOST", "importance_max_boost", 200.0),
        _ConfigField("ranking_w_rrf_raw", "SARI_RANKING_W_RRF", "ranking_w_rrf", 0.55),
        _ConfigField("ranking_w_importance_raw", "SARI_RANKING_W_IMPORTANCE", "ranking_w_importance", 0.30),
        _ConfigField("ranking_w_vector_raw", "SARI_RANKING_W_VECTOR", "ranking_w_vector", 0.15),
        _ConfigField("ranking_w_hierarchy_raw", "SARI_RANKING_W_HIERARCHY", "ranking_w_hierarchy", 0.15),
        _ConfigField("search_lsp_fallback_mode_raw", "SARI_SEARCH_LSP_FALLBACK_MODE", "search_lsp_fallback_mode", "normal", lower=True),
        _ConfigField("search_lsp_pressure_guard_enabled_raw", "SARI_SEARCH_LSP_PRESSURE_GUARD_ENABLED", "search_lsp_pressure_guard_enabled", True, lower=True),
        _ConfigField("search_lsp_pressure_pending_threshold_raw", "SARI_SEARCH_LSP_PRESSURE_PENDING_THRESHOLD", "search_lsp_pressure_pending_threshold", 1),
        _ConfigField("search_lsp_pressure_timeout_threshold_raw", "SARI_SEARCH_LSP_PRESSURE_TIMEOUT_THRESHOLD", "search_lsp_pressure_timeout_threshold", 1),
        _ConfigField("search_lsp_pressure_rejected_threshold_raw", "SARI_SEARCH_LSP_PRESSURE_REJECTED_THRESHOLD", "search_lsp_pressure_rejected_threshold", 1),
        _ConfigField("search_lsp_recent_failure_cooldown_sec_raw", "SARI_SEARCH_LSP_RECENT_FAILURE_COOLDOWN_SEC", "search_lsp_recent_failure_cooldown_sec", 5.0),
        _ConfigField("l5_call_rate_total_max_raw", "SARI_L5_CALL_RATE_TOTAL_MAX", "l5_call_rate_total_max", 0.05),
        _ConfigField("l5_call_rate_batch_max_raw", "SARI_L5_CALL_RATE_BATCH_MAX", "l5_call_rate_batch_max", 0.01),
        _ConfigField("l5_calls_per_min_per_lang_max_raw", "SARI_L5_CALLS_PER_MIN_PER_LANG_MAX", "l5_calls_per_min_per_lang_max", 30),
        _ConfigField("l5_tokens_per_10sec_global_max_raw", "SARI_L5_TOKENS_PER_10SEC_GLOBAL_MAX", "l5_tokens_per_10sec_global_max", 120),
        _ConfigField("l5_tokens_per_10sec_per_lang_max_raw", "SARI_L5_TOKENS_PER_10SEC_PER_LANG_MAX", "l5_tokens_per_10sec_per_lang_max", 30),
        _ConfigField("l5_tokens_per_10sec_per_workspace_max_raw", "SARI_L5_TOKENS_PER_10SEC_PER_WORKSPACE_MAX", "l5_tokens_per_10sec_per_workspace_max", 20),
        _ConfigField("l3_query_compile_cache_enabled_raw", "SARI_L3_QUERY_COMPILE_CACHE_ENABLED", "l3_query_compile_cache_enabled", True, lower=True),
        _ConfigField("l3_query_compile_ms_budget_raw", "SARI_L3_QUERY_COMPILE_MS_BUDGET", "l3_query_compile_ms_budget", 10.0),
        _ConfigField("l3_query_budget_ms_raw", "SARI_L3_QUERY_BUDGET_MS", "l3_query_budget_ms", 30.0),
        _ConfigField("l3_asset_mode_raw", "SARI_L3_ASSET_MODE", "l3_asset_mode", "shadow", lower=True),
        _ConfigField("l3_asset_manifest_path", "SARI_L3_ASSET_MANIFEST_PATH", "l3_asset_manifest_path", "src/sari/services/collection/assets/manifest.json"),
        _ConfigField("l3_asset_lang_allowlist_raw", "SARI_L3_ASSET_LANG_ALLOWLIST", "l3_asset_lang_allowlist", ""),
        _ConfigField("l5_db_short_circuit_enabled_raw", "SARI_L5_DB_SHORT_CIRCUIT_ENABLED", "l5_db_short_circuit_enabled", True, lower=True),
        _ConfigField("l5_db_short_circuit_log_miss_reason_raw", "SARI_L5_DB_SHORT_CIRCUIT_LOG_MISS_REASON", "l5_db_short_circuit_log_miss_reason", True, lower=True),
        _ConfigField(
            "l3_tree_sitter_executor_mode_raw",
            "SARI_L3_TREE_SITTER_EXECUTOR_MODE",
            "l3_tree_sitter_executor_mode",
            "inline",
            lower=True,
        ),
        _ConfigField(
            "l3_tree_sitter_subinterp_workers_raw",
            "SARI_L3_TREE_SITTER_SUBINTERP_WORKERS",
            "l3_tree_sitter_subinterp_workers",
            4,
        ),
        _ConfigField(
            "l3_tree_sitter_subinterp_min_bytes_raw",
            "SARI_L3_TREE_SITTER_SUBINTERP_MIN_BYTES",
            "l3_tree_sitter_subinterp_min_bytes",
            4096,
        ),
        _ConfigField(
            "l5_symbol_normalizer_executor_mode_raw",
            "SARI_L5_SYMBOL_NORMALIZER_EXECUTOR_MODE",
            "l5_symbol_normalizer_executor_mode",
            "inline",
            lower=True,
        ),
        _ConfigField(
            "l5_symbol_normalizer_subinterp_workers_raw",
            "SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_WORKERS",
            "l5_symbol_normalizer_subinterp_workers",
            2,
        ),
        _ConfigField(
            "l5_symbol_normalizer_subinterp_min_symbols_raw",
            "SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_MIN_SYMBOLS",
            "l5_symbol_normalizer_subinterp_min_symbols",
            200,
        ),
        _ConfigField("mcp_forward_to_daemon_raw", "SARI_MCP_FORWARD_TO_DAEMON", "mcp_forward_to_daemon", False, lower=True),
        _ConfigField("mcp_daemon_autostart_raw", "SARI_MCP_DAEMON_AUTOSTART", "mcp_daemon_autostart", True, lower=True),
        _ConfigField("mcp_daemon_timeout_raw", "SARI_MCP_DAEMON_TIMEOUT_SEC", "mcp_daemon_timeout_sec", 2.0),
        _ConfigField("mcp_search_call_timeout_raw", "SARI_MCP_SEARCH_CALL_TIMEOUT_SEC", "mcp_search_call_timeout_sec", 0.0),
        _ConfigField("mcp_read_call_timeout_raw", "SARI_MCP_READ_CALL_TIMEOUT_SEC", "mcp_read_call_timeout_sec", 0.0),
        _ConfigField("strict_protocol_raw", "SARI_STRICT_PROTOCOL", "strict_protocol", False, lower=True),
        _ConfigField("stabilization_enabled_raw", "SARI_STABILIZATION_ENABLED", "stabilization_enabled", True, lower=True),
        _ConfigField("http_bg_proxy_enabled_raw", "SARI_HTTP_BG_PROXY", "http_bg_proxy_enabled", False, lower=True),
        _ConfigField("http_bg_proxy_target", "SARI_HTTP_BG_PROXY_TARGET", "http_bg_proxy_target", ""),
    )


class _ConfigValueParser:
    """문자열 환경변수 값을 숫자 설정으로 안전하게 변환한다."""

    _TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
    _FALSE_VALUES = frozenset({"0", "false", "no", "off"})

    def int_min(self, raw: str, *, minimum: int, default: int) -> int:
        """정수 변환 후 하한(minimum)을 적용한다."""
        try:
            return max(minimum, int(raw))
        except ValueError:
            return default

    def int_range(self, raw: str, *, minimum: int, maximum: int, default: int) -> int:
        """정수 변환 후 [minimum, maximum] 범위를 적용한다."""
        try:
            value = int(raw)
        except ValueError:
            return default
        return min(maximum, max(minimum, value))

    def float_min(self, raw: str, *, minimum: float, default: float) -> float:
        """실수 변환 후 하한(minimum)을 적용한다."""
        try:
            return max(minimum, float(raw))
        except ValueError:
            return default

    def float_range(self, raw: str, *, minimum: float, maximum: float, default: float) -> float:
        """실수 변환 후 [minimum, maximum] 범위를 적용한다."""
        try:
            value = float(raw)
        except ValueError:
            return default
        return min(maximum, max(minimum, value))

    def parse_lane_bundle(
        self,
        *,
        hot_raw: str,
        backlog_raw: str,
        sticky_raw: str,
        switch_raw: str,
        min_lease_raw: str,
        default: tuple[int, int, float, float, int],
    ) -> tuple[int, int, float, float, int]:
        """lane 5개 값을 묶어서 파싱한다.

        기존 동작 호환:
        - 묶음 내 하나라도 파싱 실패하면 전체를 default로 되돌린다.
        """
        try:
            hot = max(0, int(hot_raw))
            backlog = max(0, int(backlog_raw))
            sticky = max(0.0, float(sticky_raw))
            switch = max(0.0, float(switch_raw))
            min_lease = max(0, int(min_lease_raw))
        except ValueError:
            return default
        return (hot, backlog, sticky, switch, min_lease)

    def bool_true(self, raw: str) -> bool:
        """전통적인 truthy 집합으로 불리언을 판정한다."""
        return raw in self._TRUE_VALUES

    def bool_enabled(self, raw: str) -> bool:
        """disable 집합에 포함되지 않으면 활성으로 본다."""
        return raw not in self._FALSE_VALUES
