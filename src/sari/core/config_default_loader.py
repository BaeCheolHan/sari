"""AppConfig 기본값 로더 구현."""

from __future__ import annotations

from pathlib import Path

from sari.core.config_fields import (
    _build_core_fields,
    _build_early_fields,
    _build_extended_fields,
    _read_config_fields,
)
from sari.core.config_helpers import (
    load_user_config as _load_user_config,
    parse_csv_setting as _parse_csv_setting,
    read_tuple_setting as _read_tuple_setting,
)
from sari.core.config_parsers import ConfigValueParser as _ConfigValueParser
from sari.core.config_profiles import (
    build_release_env_allowlist as _build_release_env_allowlist,
    normalize_run_mode as _normalize_run_mode,
    read_env_or_default as _read_env_or_default,
)


def build_default_config(cls):
    """`AppConfig.default()` 실제 로더 구현."""
    # 순환 import를 피하기 위해 DTO 타입만 지연 로드한다.
    from sari.core import config_model as _m

    CollectionRuntimeConfigDTO = _m.CollectionRuntimeConfigDTO
    LspHubRuntimeConfigDTO = _m.LspHubRuntimeConfigDTO
    SearchRuntimeConfigDTO = _m.SearchRuntimeConfigDTO

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
    early_run_mode = _normalize_run_mode(early_raw_values["run_mode_raw"])
    # release 모드에서만 env surface를 최소화하고, 나머지는 기존 동작(모든 env 허용)을 유지한다.
    release_allow_env_keys = _build_release_env_allowlist() if early_run_mode == "release" else None
    core_raw_values = _read_config_fields(
        file_config=file_config,
        fields=_build_core_fields(file_config=file_config, defaults=cls),
        allow_env_keys=release_allow_env_keys,
    )
    retry_max_raw = core_raw_values["retry_max_raw"]
    backoff_raw = core_raw_values["backoff_raw"]
    poll_raw = core_raw_values["poll_raw"]
    debounce_raw = core_raw_values["debounce_raw"]
    worker_raw = core_raw_values["worker_raw"]
    l5_worker_count_raw = core_raw_values["l5_worker_count_raw"]
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
    lsp_bulk_max_per_repo_lang_raw = core_raw_values["lsp_bulk_max_per_repo_lang_raw"]
    lsp_interactive_reserved_slots_raw = core_raw_values["lsp_interactive_reserved_slots_raw"]
    lsp_interactive_timeout_raw = core_raw_values["lsp_interactive_timeout_raw"]
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
        allow_env_keys=release_allow_env_keys,
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
    lsp_scope_java_markers_raw = extended_raw_values["lsp_scope_java_markers_raw"]
    lsp_scope_ts_markers_raw = extended_raw_values["lsp_scope_ts_markers_raw"]
    lsp_scope_vue_markers_raw = extended_raw_values["lsp_scope_vue_markers_raw"]
    lsp_scope_top_level_fallback_raw = extended_raw_values["lsp_scope_top_level_fallback_raw"]
    lsp_scope_active_languages_raw = extended_raw_values["lsp_scope_active_languages_raw"]
    lsp_session_broker_enabled_raw = extended_raw_values["lsp_session_broker_enabled_raw"]
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
    l3_query_compile_ms_budget_raw = extended_raw_values["l3_query_compile_ms_budget_raw"]
    l3_query_budget_ms_raw = extended_raw_values["l3_query_budget_ms_raw"]
    l5_db_short_circuit_enabled_raw = extended_raw_values["l5_db_short_circuit_enabled_raw"]
    l3_tree_sitter_executor_mode_raw = extended_raw_values["l3_tree_sitter_executor_mode_raw"]
    l3_tree_sitter_subinterp_workers_raw = extended_raw_values["l3_tree_sitter_subinterp_workers_raw"]
    l3_tree_sitter_subinterp_min_bytes_raw = extended_raw_values["l3_tree_sitter_subinterp_min_bytes_raw"]
    l5_symbol_normalizer_executor_mode_raw = extended_raw_values["l5_symbol_normalizer_executor_mode_raw"]
    l5_symbol_normalizer_subinterp_workers_raw = extended_raw_values["l5_symbol_normalizer_subinterp_workers_raw"]
    l5_symbol_normalizer_subinterp_min_symbols_raw = extended_raw_values["l5_symbol_normalizer_subinterp_min_symbols_raw"]
    mcp_forward_to_daemon_raw = extended_raw_values["mcp_forward_to_daemon_raw"]
    mcp_daemon_autostart_raw = extended_raw_values["mcp_daemon_autostart_raw"]
    mcp_daemon_timeout_raw = extended_raw_values["mcp_daemon_timeout_raw"]
    parser = _ConfigValueParser()
    normalized_run_mode = _normalize_run_mode(run_mode_raw)
    run_mode = "prod" if normalized_run_mode in {"prod", "release"} else "dev"
    retry_max = parser.int_min(retry_max_raw, minimum=1, default=5)
    backoff_sec = parser.int_min(backoff_raw, minimum=1, default=1)
    poll_ms = parser.int_min(poll_raw, minimum=100, default=100)
    debounce_ms = parser.int_min(debounce_raw, minimum=50, default=150)
    worker_count = parser.int_min(worker_raw, minimum=1, default=4)
    l5_worker_count = parser.int_min(l5_worker_count_raw, minimum=1, default=2)
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
    lsp_symbol_info_budget_sec = parser.float_min(lsp_symbol_info_budget_raw, minimum=0.0, default=10.0)
    l3_executor_max_workers = parser.int_min(l3_executor_max_workers_raw, minimum=0, default=0)
    l3_recent_success_ttl_sec = parser.int_min(l3_recent_success_ttl_raw, minimum=0, default=120)
    l3_backpressure_cooldown_ms = parser.int_min(l3_backpressure_cooldown_ms_raw, minimum=10, default=300)
    lsp_scale_out_hot_hits = parser.int_min(lsp_scale_out_hot_hits_raw, minimum=2, default=8)
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
    l5_call_rate_total_max = parser.float_range(l5_call_rate_total_max_raw, minimum=0.0, maximum=1.0, default=0.10)
    l5_call_rate_batch_max = parser.float_range(l5_call_rate_batch_max_raw, minimum=0.0, maximum=1.0, default=0.05)
    l5_calls_per_min_per_lang_max = parser.int_min(l5_calls_per_min_per_lang_max_raw, minimum=1, default=60)
    l5_tokens_per_10sec_global_max = parser.int_min(l5_tokens_per_10sec_global_max_raw, minimum=1, default=240)
    l5_tokens_per_10sec_per_lang_max = parser.int_min(l5_tokens_per_10sec_per_lang_max_raw, minimum=1, default=60)
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
        default=2048,
    )
    l5_symbol_normalizer_subinterp_workers = parser.int_min(
        l5_symbol_normalizer_subinterp_workers_raw,
        minimum=1,
        default=2,
    )
    l5_symbol_normalizer_subinterp_min_symbols = parser.int_min(
        l5_symbol_normalizer_subinterp_min_symbols_raw,
        minimum=0,
        default=100,
    )
    include_ext_raw = _read_env_or_default(
        env_key="SARI_COLLECTION_INCLUDE_EXT",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    exclude_globs_raw = _read_env_or_default(
        env_key="SARI_COLLECTION_EXCLUDE_GLOBS",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    vector_apply_types_raw = _read_env_or_default(
        env_key="SARI_VECTOR_APPLY_TO_ITEM_TYPES",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    importance_core_paths_raw = _read_env_or_default(
        env_key="SARI_IMPORTANCE_CORE_PATH_TOKENS",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    importance_noisy_paths_raw = _read_env_or_default(
        env_key="SARI_IMPORTANCE_NOISY_PATH_TOKENS",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    importance_code_ext_raw = _read_env_or_default(
        env_key="SARI_IMPORTANCE_CODE_EXTENSIONS",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    importance_noisy_ext_raw = _read_env_or_default(
        env_key="SARI_IMPORTANCE_NOISY_EXTENSIONS",
        default="",
        allow_env_keys=release_allow_env_keys,
    )
    include_ext = _parse_csv_setting(
        include_ext_raw,
        default_value=_read_tuple_setting(file_config, "collection_include_ext", cls.collection_include_ext),
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
        bulk_mode_enabled=True,
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
        pipeline_l5_worker_count=l5_worker_count,
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
        lsp_bulk_max_instances_per_repo_language=lsp_hub_runtime.bulk_max_instances_per_repo_language,
        lsp_interactive_reserved_slots_per_repo_language=lsp_hub_runtime.interactive_reserved_slots_per_repo_language,
        lsp_interactive_timeout_sec=lsp_hub_runtime.interactive_timeout_sec,
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
        lsp_scope_java_markers=lsp_scope_java_markers,
        lsp_scope_ts_markers=lsp_scope_ts_markers,
        lsp_scope_vue_markers=lsp_scope_vue_markers,
        lsp_scope_top_level_fallback=parser.bool_true(lsp_scope_top_level_fallback_raw),
        lsp_scope_active_languages=lsp_scope_active_languages,
        lsp_session_broker_enabled=parser.bool_true(lsp_session_broker_enabled_raw),
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
        l3_query_compile_ms_budget=l3_query_compile_ms_budget,
        l3_query_budget_ms=l3_query_budget_ms,
        l3_tree_sitter_executor_mode=(
            l3_tree_sitter_executor_mode_raw
            if l3_tree_sitter_executor_mode_raw in {"inline", "subinterp"}
            else "subinterp"
        ),
        l3_tree_sitter_subinterp_workers=l3_tree_sitter_subinterp_workers,
        l3_tree_sitter_subinterp_min_bytes=l3_tree_sitter_subinterp_min_bytes,
        l5_db_short_circuit_enabled=parser.bool_true(l5_db_short_circuit_enabled_raw),
        l5_symbol_normalizer_executor_mode=(
            l5_symbol_normalizer_executor_mode_raw
            if l5_symbol_normalizer_executor_mode_raw in {"inline", "subinterp"}
            else "subinterp"
        ),
        l5_symbol_normalizer_subinterp_workers=l5_symbol_normalizer_subinterp_workers,
        l5_symbol_normalizer_subinterp_min_symbols=l5_symbol_normalizer_subinterp_min_symbols,
        mcp_forward_to_daemon=parser.bool_true(mcp_forward_to_daemon_raw),
        mcp_daemon_autostart=parser.bool_true(mcp_daemon_autostart_raw),
        mcp_daemon_timeout_sec=mcp_daemon_timeout_sec,
    )
