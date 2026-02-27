"""AppConfig raw 필드 선언 및 로딩 유틸."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sari.core.config_helpers import read_tuple_setting as _read_tuple_setting

if TYPE_CHECKING:
    from sari.core.config_model import AppConfig


@dataclass(frozen=True)
class _ConfigField:
    """환경변수/파일 키를 묶어 raw 문자열 값을 읽기 위한 선언형 필드다."""

    name: str
    env_key: str
    file_key: str
    default: object
    lower: bool = False


def _read_config_fields(
    *,
    file_config: dict[str, object],
    fields: tuple[_ConfigField, ...],
    allow_env_keys: set[str] | None = None,
) -> dict[str, str]:
    """선언된 필드 목록을 일괄 로드해 raw 문자열 맵으로 반환한다."""
    resolved: dict[str, str] = {}
    for field in fields:
        if allow_env_keys is None or field.env_key in allow_env_keys:
            raw = os.getenv(field.env_key, str(file_config.get(field.file_key, field.default))).strip()
        else:
            raw = str(file_config.get(field.file_key, field.default)).strip()
        resolved[field.name] = raw.lower() if field.lower else raw
    return resolved


def _build_early_fields() -> tuple[_ConfigField, ...]:
    """default() 초기 부트스트랩 필드 목록."""
    return (
        _ConfigField("db_path_raw", "SARI_DB_PATH", "db_path", ""),
        _ConfigField("backend_raw", "SARI_CANDIDATE_BACKEND", "candidate_backend", "tantivy", lower=True),
        _ConfigField("fallback_flag", "SARI_CANDIDATE_FALLBACK_SCAN", "candidate_fallback_scan", 1),
        _ConfigField("run_mode_raw", "SARI_RUN_MODE", "run_mode", "prod", lower=True),
    )


def _build_core_fields(*, file_config: dict[str, object], defaults: type[AppConfig]) -> tuple[_ConfigField, ...]:
    """pipeline/L3/LSP 핵심 설정 필드 목록."""
    return (
        _ConfigField("retry_max_raw", "SARI_PIPELINE_RETRY_MAX", "pipeline_retry_max", 5),
        _ConfigField("backoff_raw", "SARI_PIPELINE_BACKOFF_BASE_SEC", "pipeline_backoff_base_sec", 1),
        _ConfigField("poll_raw", "SARI_QUEUE_POLL_INTERVAL_MS", "queue_poll_interval_ms", 100),
        _ConfigField("debounce_raw", "SARI_WATCHER_DEBOUNCE_MS", "watcher_debounce_ms", 150),
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
        _ConfigField("lsp_scale_out_hot_hits_raw", "SARI_LSP_SCALE_OUT_HOT_HITS", "lsp_scale_out_hot_hits", 8),
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
        _ConfigField("l5_call_rate_total_max_raw", "SARI_L5_CALL_RATE_TOTAL_MAX", "l5_call_rate_total_max", 0.10),
        _ConfigField("l5_call_rate_batch_max_raw", "SARI_L5_CALL_RATE_BATCH_MAX", "l5_call_rate_batch_max", 0.05),
        _ConfigField("l5_calls_per_min_per_lang_max_raw", "SARI_L5_CALLS_PER_MIN_PER_LANG_MAX", "l5_calls_per_min_per_lang_max", 60),
        _ConfigField("l5_tokens_per_10sec_global_max_raw", "SARI_L5_TOKENS_PER_10SEC_GLOBAL_MAX", "l5_tokens_per_10sec_global_max", 240),
        _ConfigField("l5_tokens_per_10sec_per_lang_max_raw", "SARI_L5_TOKENS_PER_10SEC_PER_LANG_MAX", "l5_tokens_per_10sec_per_lang_max", 60),
        _ConfigField("l5_tokens_per_10sec_per_workspace_max_raw", "SARI_L5_TOKENS_PER_10SEC_PER_WORKSPACE_MAX", "l5_tokens_per_10sec_per_workspace_max", 20),
        _ConfigField("l3_query_compile_cache_enabled_raw", "SARI_L3_QUERY_COMPILE_CACHE_ENABLED", "l3_query_compile_cache_enabled", True, lower=True),
        _ConfigField("l3_query_compile_ms_budget_raw", "SARI_L3_QUERY_COMPILE_MS_BUDGET", "l3_query_compile_ms_budget", 10.0),
        _ConfigField("l3_query_budget_ms_raw", "SARI_L3_QUERY_BUDGET_MS", "l3_query_budget_ms", 30.0),
        _ConfigField("l3_asset_mode_raw", "SARI_L3_ASSET_MODE", "l3_asset_mode", "shadow", lower=True),
        _ConfigField("l3_asset_lang_allowlist_raw", "SARI_L3_ASSET_LANG_ALLOWLIST", "l3_asset_lang_allowlist", ""),
        _ConfigField("l5_db_short_circuit_enabled_raw", "SARI_L5_DB_SHORT_CIRCUIT_ENABLED", "l5_db_short_circuit_enabled", True, lower=True),
        _ConfigField("l5_db_short_circuit_log_miss_reason_raw", "SARI_L5_DB_SHORT_CIRCUIT_LOG_MISS_REASON", "l5_db_short_circuit_log_miss_reason", True, lower=True),
        _ConfigField(
            "l3_tree_sitter_executor_mode_raw",
            "SARI_L3_TREE_SITTER_EXECUTOR_MODE",
            "l3_tree_sitter_executor_mode",
            "subinterp",
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
            2048,
        ),
        _ConfigField(
            "l5_symbol_normalizer_executor_mode_raw",
            "SARI_L5_SYMBOL_NORMALIZER_EXECUTOR_MODE",
            "l5_symbol_normalizer_executor_mode",
            "subinterp",
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
            100,
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
