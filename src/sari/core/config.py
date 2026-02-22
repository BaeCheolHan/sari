"""런타임 설정을 정의한다."""

import os
import json
from dataclasses import dataclass
from pathlib import Path

from sari.core.language_registry import get_default_collection_extensions, get_enabled_language_names

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
    lsp_max_instances_per_repo_language: int = 2
    lsp_bulk_mode_enabled: bool = True
    lsp_bulk_max_instances_per_repo_language: int = 4
    lsp_interactive_reserved_slots_per_repo_language: int = 1
    lsp_interactive_timeout_sec: float = 2.5
    lsp_interactive_queue_max: int = 256
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
    lsp_session_broker_enabled: bool = True
    lsp_session_broker_metrics_enabled: bool = True
    lsp_broker_optional_scaffolding_enabled: bool = False
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
    mcp_forward_to_daemon: bool = False
    mcp_daemon_autostart: bool = True
    mcp_daemon_timeout_sec: float = 2.0
    strict_protocol: bool = False
    stabilization_enabled: bool = True
    http_bg_proxy_enabled: bool = False
    http_bg_proxy_target: str = ""

    @classmethod
    def default(cls) -> "AppConfig":
        """기본 설정값으로 구성 객체를 생성한다."""
        file_config = _load_user_config()
        db_path_raw = os.getenv("SARI_DB_PATH", str(file_config.get("db_path", ""))).strip()
        db_path = Path(db_path_raw).expanduser() if db_path_raw != "" else Path.home() / ".local" / "share" / "sari-v2" / "state.db"
        backend = os.getenv("SARI_CANDIDATE_BACKEND", "tantivy").strip().lower()
        if backend not in {"tantivy", "scan"}:
            backend = "tantivy"
        fallback_flag = os.getenv("SARI_CANDIDATE_FALLBACK_SCAN", "1").strip()
        retry_max_raw = os.getenv("SARI_PIPELINE_RETRY_MAX", str(file_config.get("pipeline_retry_max", 5))).strip()
        backoff_raw = os.getenv("SARI_PIPELINE_BACKOFF_BASE_SEC", str(file_config.get("pipeline_backoff_base_sec", 1))).strip()
        poll_raw = os.getenv("SARI_QUEUE_POLL_INTERVAL_MS", str(file_config.get("queue_poll_interval_ms", 500))).strip()
        debounce_raw = os.getenv("SARI_WATCHER_DEBOUNCE_MS", str(file_config.get("watcher_debounce_ms", 300))).strip()
        worker_raw = os.getenv("SARI_PIPELINE_WORKER_COUNT", str(file_config.get("pipeline_worker_count", 4))).strip()
        p95_raw = os.getenv("SARI_PIPELINE_L3_P95_THRESHOLD_MS", str(file_config.get("pipeline_l3_p95_threshold_ms", 180000))).strip()
        dead_ratio_raw = os.getenv("SARI_PIPELINE_DEAD_RATIO_THRESHOLD_BPS", str(file_config.get("pipeline_dead_ratio_threshold_bps", 10))).strip()
        alert_window_raw = os.getenv("SARI_PIPELINE_ALERT_WINDOW_SEC", str(file_config.get("pipeline_alert_window_sec", 300))).strip()
        auto_tick_raw = os.getenv("SARI_PIPELINE_AUTO_TICK_INTERVAL_SEC", str(file_config.get("pipeline_auto_tick_interval_sec", 5))).strip()
        l3_parallel_enabled_raw = os.getenv("SARI_L3_PARALLEL_ENABLED", str(file_config.get("l3_parallel_enabled", True))).strip().lower()
        run_mode_raw = os.getenv("SARI_RUN_MODE", str(file_config.get("run_mode", "prod"))).strip().lower()
        heartbeat_raw = os.getenv("SARI_DAEMON_HEARTBEAT_INTERVAL_SEC", str(file_config.get("daemon_heartbeat_interval_sec", 2))).strip()
        stale_timeout_raw = os.getenv("SARI_DAEMON_STALE_TIMEOUT_SEC", str(file_config.get("daemon_stale_timeout_sec", 15))).strip()
        lsp_timeout_raw = os.getenv("SARI_LSP_REQUEST_TIMEOUT_SEC", str(file_config.get("lsp_request_timeout_sec", 20.0))).strip()
        lsp_max_per_repo_lang_raw = os.getenv(
            "SARI_LSP_MAX_INSTANCES_PER_REPO_LANGUAGE",
            str(file_config.get("lsp_max_instances_per_repo_language", 2)),
        ).strip()
        lsp_bulk_mode_enabled_raw = os.getenv(
            "SARI_LSP_BULK_MODE_ENABLED",
            str(file_config.get("lsp_bulk_mode_enabled", True)),
        ).strip().lower()
        lsp_bulk_max_per_repo_lang_raw = os.getenv(
            "SARI_LSP_BULK_MAX_INSTANCES_PER_REPO_LANGUAGE",
            str(file_config.get("lsp_bulk_max_instances_per_repo_language", 4)),
        ).strip()
        lsp_interactive_reserved_slots_raw = os.getenv(
            "SARI_LSP_INTERACTIVE_RESERVED_SLOTS_PER_REPO_LANGUAGE",
            str(file_config.get("lsp_interactive_reserved_slots_per_repo_language", 1)),
        ).strip()
        lsp_interactive_timeout_raw = os.getenv(
            "SARI_LSP_INTERACTIVE_TIMEOUT_SEC",
            str(file_config.get("lsp_interactive_timeout_sec", 2.5)),
        ).strip()
        lsp_interactive_queue_max_raw = os.getenv(
            "SARI_LSP_INTERACTIVE_QUEUE_MAX",
            str(file_config.get("lsp_interactive_queue_max", 256)),
        ).strip()
        lsp_global_soft_limit_raw = os.getenv(
            "SARI_LSP_GLOBAL_SOFT_LIMIT",
            str(file_config.get("lsp_global_soft_limit", 0)),
        ).strip()
        l3_executor_max_workers_raw = os.getenv(
            "SARI_L3_EXECUTOR_MAX_WORKERS",
            str(file_config.get("l3_executor_max_workers", 0)),
        ).strip()
        l3_recent_success_ttl_raw = os.getenv(
            "SARI_L3_RECENT_SUCCESS_TTL_SEC",
            str(file_config.get("l3_recent_success_ttl_sec", 120)),
        ).strip()
        l3_backpressure_on_interactive_raw = os.getenv(
            "SARI_L3_BACKPRESSURE_ON_INTERACTIVE",
            str(file_config.get("l3_backpressure_on_interactive", True)),
        ).strip().lower()
        l3_backpressure_cooldown_ms_raw = os.getenv(
            "SARI_L3_BACKPRESSURE_COOLDOWN_MS",
            str(file_config.get("l3_backpressure_cooldown_ms", 300)),
        ).strip()
        l3_supported_languages_raw = os.getenv(
            "SARI_L3_SUPPORTED_LANGUAGES",
            str(",".join(_read_tuple_setting(file_config, "l3_supported_languages", cls.l3_supported_languages))),
        ).strip()
        lsp_scale_out_hot_hits_raw = os.getenv(
            "SARI_LSP_SCALE_OUT_HOT_HITS",
            str(file_config.get("lsp_scale_out_hot_hits", 24)),
        ).strip()
        lsp_file_buffer_idle_ttl_raw = os.getenv(
            "SARI_LSP_FILE_BUFFER_IDLE_TTL_SEC",
            str(file_config.get("lsp_file_buffer_idle_ttl_sec", 20.0)),
        ).strip()
        lsp_file_buffer_max_open_raw = os.getenv(
            "SARI_LSP_FILE_BUFFER_MAX_OPEN",
            str(file_config.get("lsp_file_buffer_max_open", 512)),
        ).strip()
        lsp_java_min_major_raw = os.getenv(
            "SARI_LSP_JAVA_MIN_MAJOR",
            str(file_config.get("lsp_java_min_major", 17)),
        ).strip()
        lsp_probe_timeout_default_raw = os.getenv(
            "SARI_LSP_PROBE_TIMEOUT_DEFAULT_SEC",
            str(file_config.get("lsp_probe_timeout_default_sec", 20.0)),
        ).strip()
        lsp_probe_timeout_go_raw = os.getenv(
            "SARI_LSP_PROBE_TIMEOUT_GO_SEC",
            str(file_config.get("lsp_probe_timeout_go_sec", 45.0)),
        ).strip()
        lsp_probe_workers_raw = os.getenv(
            "SARI_LSP_PROBE_WORKERS",
            str(file_config.get("lsp_probe_workers", 8)),
        ).strip()
        lsp_probe_l1_workers_raw = os.getenv(
            "SARI_LSP_PROBE_L1_WORKERS",
            str(file_config.get("lsp_probe_l1_workers", 4)),
        ).strip()
        lsp_probe_force_join_ms_raw = os.getenv(
            "SARI_LSP_PROBE_FORCE_JOIN_MS",
            str(file_config.get("lsp_probe_force_join_ms", 300)),
        ).strip()
        lsp_probe_warming_retry_sec_raw = os.getenv(
            "SARI_LSP_PROBE_WARMING_RETRY_SEC",
            str(file_config.get("lsp_probe_warming_retry_sec", 5)),
        ).strip()
        lsp_probe_warming_threshold_raw = os.getenv(
            "SARI_LSP_PROBE_WARMING_THRESHOLD",
            str(file_config.get("lsp_probe_warming_threshold", 6)),
        ).strip()
        lsp_probe_permanent_backoff_sec_raw = os.getenv(
            "SARI_LSP_PROBE_PERMANENT_BACKOFF_SEC",
            str(file_config.get("lsp_probe_permanent_backoff_sec", 1800)),
        ).strip()
        lsp_probe_bootstrap_file_window_raw = os.getenv(
            "SARI_LSP_PROBE_BOOTSTRAP_FILE_WINDOW",
            str(file_config.get("lsp_probe_bootstrap_file_window", 256)),
        ).strip()
        lsp_probe_bootstrap_top_k_raw = os.getenv(
            "SARI_LSP_PROBE_BOOTSTRAP_TOP_K",
            str(file_config.get("lsp_probe_bootstrap_top_k", 3)),
        ).strip()
        lsp_probe_language_priority_raw = os.getenv(
            "SARI_LSP_PROBE_LANGUAGE_PRIORITY",
            str(",".join(_read_tuple_setting(file_config, "lsp_probe_language_priority", cls.lsp_probe_language_priority))),
        ).strip()
        lsp_probe_l1_languages_raw = os.getenv(
            "SARI_LSP_PROBE_L1_LANGUAGES",
            str(",".join(_read_tuple_setting(file_config, "lsp_probe_l1_languages", cls.lsp_probe_l1_languages))),
        ).strip()
        lsp_scope_planner_enabled_raw = os.getenv(
            "SARI_LSP_SCOPE_PLANNER_ENABLED",
            str(file_config.get("lsp_scope_planner_enabled", cls.lsp_scope_planner_enabled)),
        ).strip().lower()
        lsp_scope_planner_shadow_mode_raw = os.getenv(
            "SARI_LSP_SCOPE_PLANNER_SHADOW_MODE",
            str(file_config.get("lsp_scope_planner_shadow_mode", cls.lsp_scope_planner_shadow_mode)),
        ).strip().lower()
        lsp_scope_java_markers_raw = os.getenv(
            "SARI_LSP_SCOPE_JAVA_MARKERS",
            str(",".join(_read_tuple_setting(file_config, "lsp_scope_java_markers", cls.lsp_scope_java_markers))),
        ).strip()
        lsp_scope_ts_markers_raw = os.getenv(
            "SARI_LSP_SCOPE_TS_MARKERS",
            str(",".join(_read_tuple_setting(file_config, "lsp_scope_ts_markers", cls.lsp_scope_ts_markers))),
        ).strip()
        lsp_scope_vue_markers_raw = os.getenv(
            "SARI_LSP_SCOPE_VUE_MARKERS",
            str(",".join(_read_tuple_setting(file_config, "lsp_scope_vue_markers", cls.lsp_scope_vue_markers))),
        ).strip()
        lsp_scope_top_level_fallback_raw = os.getenv(
            "SARI_LSP_SCOPE_TOP_LEVEL_FALLBACK",
            str(file_config.get("lsp_scope_top_level_fallback", cls.lsp_scope_top_level_fallback)),
        ).strip().lower()
        lsp_session_broker_enabled_raw = os.getenv(
            "SARI_LSP_SESSION_BROKER_ENABLED",
            str(file_config.get("lsp_session_broker_enabled", cls.lsp_session_broker_enabled)),
        ).strip().lower()
        lsp_session_broker_metrics_enabled_raw = os.getenv(
            "SARI_LSP_SESSION_BROKER_METRICS_ENABLED",
            str(file_config.get("lsp_session_broker_metrics_enabled", cls.lsp_session_broker_metrics_enabled)),
        ).strip().lower()
        lsp_broker_optional_scaffolding_enabled_raw = os.getenv(
            "SARI_LSP_BROKER_OPTIONAL_SCAFFOLDING_ENABLED",
            str(file_config.get("lsp_broker_optional_scaffolding_enabled", cls.lsp_broker_optional_scaffolding_enabled)),
        ).strip().lower()
        lsp_hotness_event_window_sec_raw = os.getenv(
            "SARI_LSP_HOTNESS_EVENT_WINDOW_SEC",
            str(file_config.get("lsp_hotness_event_window_sec", cls.lsp_hotness_event_window_sec)),
        ).strip()
        lsp_hotness_decay_window_sec_raw = os.getenv(
            "SARI_LSP_HOTNESS_DECAY_WINDOW_SEC",
            str(file_config.get("lsp_hotness_decay_window_sec", cls.lsp_hotness_decay_window_sec)),
        ).strip()
        lsp_broker_backlog_min_share_raw = os.getenv(
            "SARI_LSP_BROKER_BACKLOG_MIN_SHARE",
            str(file_config.get("lsp_broker_backlog_min_share", cls.lsp_broker_backlog_min_share)),
        ).strip()
        lsp_broker_max_standby_sessions_per_lang_raw = os.getenv(
            "SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_LANG",
            str(file_config.get("lsp_broker_max_standby_sessions_per_lang", cls.lsp_broker_max_standby_sessions_per_lang)),
        ).strip()
        lsp_broker_max_standby_sessions_per_budget_group_raw = os.getenv(
            "SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_BUDGET_GROUP",
            str(
                file_config.get(
                    "lsp_broker_max_standby_sessions_per_budget_group",
                    cls.lsp_broker_max_standby_sessions_per_budget_group,
                )
            ),
        ).strip()
        lsp_broker_ts_vue_active_cap_raw = os.getenv(
            "SARI_LSP_BROKER_TS_VUE_ACTIVE_CAP",
            str(file_config.get("lsp_broker_ts_vue_active_cap", cls.lsp_broker_ts_vue_active_cap)),
        ).strip()
        lsp_broker_java_hot_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_JAVA_HOT_LANES",
            str(file_config.get("lsp_broker_java_hot_lanes", cls.lsp_broker_java_hot_lanes)),
        ).strip()
        lsp_broker_java_backlog_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_JAVA_BACKLOG_LANES",
            str(file_config.get("lsp_broker_java_backlog_lanes", cls.lsp_broker_java_backlog_lanes)),
        ).strip()
        lsp_broker_java_sticky_ttl_sec_raw = os.getenv(
            "SARI_LSP_BROKER_JAVA_STICKY_TTL_SEC",
            str(file_config.get("lsp_broker_java_sticky_ttl_sec", cls.lsp_broker_java_sticky_ttl_sec)),
        ).strip()
        lsp_broker_java_switch_cooldown_sec_raw = os.getenv(
            "SARI_LSP_BROKER_JAVA_SWITCH_COOLDOWN_SEC",
            str(file_config.get("lsp_broker_java_switch_cooldown_sec", cls.lsp_broker_java_switch_cooldown_sec)),
        ).strip()
        lsp_broker_java_min_lease_ms_raw = os.getenv(
            "SARI_LSP_BROKER_JAVA_MIN_LEASE_MS",
            str(file_config.get("lsp_broker_java_min_lease_ms", cls.lsp_broker_java_min_lease_ms)),
        ).strip()
        lsp_broker_ts_hot_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_TS_HOT_LANES",
            str(file_config.get("lsp_broker_ts_hot_lanes", cls.lsp_broker_ts_hot_lanes)),
        ).strip()
        lsp_broker_ts_backlog_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_TS_BACKLOG_LANES",
            str(file_config.get("lsp_broker_ts_backlog_lanes", cls.lsp_broker_ts_backlog_lanes)),
        ).strip()
        lsp_broker_ts_sticky_ttl_sec_raw = os.getenv(
            "SARI_LSP_BROKER_TS_STICKY_TTL_SEC",
            str(file_config.get("lsp_broker_ts_sticky_ttl_sec", cls.lsp_broker_ts_sticky_ttl_sec)),
        ).strip()
        lsp_broker_ts_switch_cooldown_sec_raw = os.getenv(
            "SARI_LSP_BROKER_TS_SWITCH_COOLDOWN_SEC",
            str(file_config.get("lsp_broker_ts_switch_cooldown_sec", cls.lsp_broker_ts_switch_cooldown_sec)),
        ).strip()
        lsp_broker_ts_min_lease_ms_raw = os.getenv(
            "SARI_LSP_BROKER_TS_MIN_LEASE_MS",
            str(file_config.get("lsp_broker_ts_min_lease_ms", cls.lsp_broker_ts_min_lease_ms)),
        ).strip()
        lsp_broker_vue_hot_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_VUE_HOT_LANES",
            str(file_config.get("lsp_broker_vue_hot_lanes", cls.lsp_broker_vue_hot_lanes)),
        ).strip()
        lsp_broker_vue_backlog_lanes_raw = os.getenv(
            "SARI_LSP_BROKER_VUE_BACKLOG_LANES",
            str(file_config.get("lsp_broker_vue_backlog_lanes", cls.lsp_broker_vue_backlog_lanes)),
        ).strip()
        lsp_broker_vue_sticky_ttl_sec_raw = os.getenv(
            "SARI_LSP_BROKER_VUE_STICKY_TTL_SEC",
            str(file_config.get("lsp_broker_vue_sticky_ttl_sec", cls.lsp_broker_vue_sticky_ttl_sec)),
        ).strip()
        lsp_broker_vue_switch_cooldown_sec_raw = os.getenv(
            "SARI_LSP_BROKER_VUE_SWITCH_COOLDOWN_SEC",
            str(file_config.get("lsp_broker_vue_switch_cooldown_sec", cls.lsp_broker_vue_switch_cooldown_sec)),
        ).strip()
        lsp_broker_vue_min_lease_ms_raw = os.getenv(
            "SARI_LSP_BROKER_VUE_MIN_LEASE_MS",
            str(file_config.get("lsp_broker_vue_min_lease_ms", cls.lsp_broker_vue_min_lease_ms)),
        ).strip()
        lsp_max_concurrent_starts_raw = os.getenv(
            "SARI_LSP_MAX_CONCURRENT_STARTS",
            str(file_config.get("lsp_max_concurrent_starts", 4)),
        ).strip()
        lsp_max_concurrent_l1_probes_raw = os.getenv(
            "SARI_LSP_MAX_CONCURRENT_L1_PROBES",
            str(file_config.get("lsp_max_concurrent_l1_probes", 4)),
        ).strip()
        orphan_check_raw = os.getenv("SARI_ORPHAN_PPID_CHECK_INTERVAL_SEC", str(file_config.get("orphan_ppid_check_interval_sec", 1))).strip()
        shutdown_join_raw = os.getenv("SARI_SHUTDOWN_JOIN_TIMEOUT_SEC", str(file_config.get("shutdown_join_timeout_sec", 2))).strip()
        vector_enabled_raw = os.getenv("SARI_VECTOR_ENABLED", str(file_config.get("vector_enabled", False))).strip().lower()
        vector_model_id = str(file_config.get("vector_model_id", "hashbow-v1")).strip()
        vector_dim_raw = os.getenv("SARI_VECTOR_DIM", str(file_config.get("vector_dim", 128))).strip()
        vector_candidate_raw = os.getenv("SARI_VECTOR_CANDIDATE_K", str(file_config.get("vector_candidate_k", 50))).strip()
        vector_rerank_raw = os.getenv("SARI_VECTOR_RERANK_K", str(file_config.get("vector_rerank_k", 20))).strip()
        vector_blend_raw = os.getenv("SARI_VECTOR_BLEND_WEIGHT", str(file_config.get("vector_blend_weight", 0.2))).strip()
        vector_min_similarity_raw = os.getenv(
            "SARI_VECTOR_MIN_SIMILARITY_THRESHOLD",
            str(file_config.get("vector_min_similarity_threshold", 0.15)),
        ).strip()
        vector_max_boost_raw = os.getenv("SARI_VECTOR_MAX_BOOST", str(file_config.get("vector_max_boost", 0.2))).strip()
        vector_min_token_raw = os.getenv(
            "SARI_VECTOR_MIN_TOKEN_COUNT_FOR_RERANK",
            str(file_config.get("vector_min_token_count_for_rerank", 2)),
        ).strip()
        importance_normalize_mode = str(
            os.getenv("SARI_IMPORTANCE_NORMALIZE_MODE", str(file_config.get("importance_normalize_mode", "log1p")))
        ).strip()
        importance_max_boost_raw = os.getenv(
            "SARI_IMPORTANCE_MAX_BOOST",
            str(file_config.get("importance_max_boost", 200.0)),
        ).strip()
        ranking_w_rrf_raw = os.getenv("SARI_RANKING_W_RRF", str(file_config.get("ranking_w_rrf", 0.55))).strip()
        ranking_w_importance_raw = os.getenv(
            "SARI_RANKING_W_IMPORTANCE",
            str(file_config.get("ranking_w_importance", 0.30)),
        ).strip()
        ranking_w_vector_raw = os.getenv(
            "SARI_RANKING_W_VECTOR",
            str(file_config.get("ranking_w_vector", 0.15)),
        ).strip()
        ranking_w_hierarchy_raw = os.getenv(
            "SARI_RANKING_W_HIERARCHY",
            str(file_config.get("ranking_w_hierarchy", 0.15)),
        ).strip()
        search_lsp_fallback_mode_raw = os.getenv(
            "SARI_SEARCH_LSP_FALLBACK_MODE",
            str(file_config.get("search_lsp_fallback_mode", "normal")),
        ).strip().lower()
        mcp_forward_to_daemon_raw = os.getenv("SARI_MCP_FORWARD_TO_DAEMON", str(file_config.get("mcp_forward_to_daemon", False))).strip().lower()
        mcp_daemon_autostart_raw = os.getenv("SARI_MCP_DAEMON_AUTOSTART", str(file_config.get("mcp_daemon_autostart", True))).strip().lower()
        mcp_daemon_timeout_raw = os.getenv("SARI_MCP_DAEMON_TIMEOUT_SEC", str(file_config.get("mcp_daemon_timeout_sec", 2.0))).strip()
        strict_protocol_raw = os.getenv("SARI_STRICT_PROTOCOL", str(file_config.get("strict_protocol", False))).strip().lower()
        stabilization_enabled_raw = os.getenv("SARI_STABILIZATION_ENABLED", str(file_config.get("stabilization_enabled", True))).strip().lower()
        http_bg_proxy_enabled_raw = os.getenv("SARI_HTTP_BG_PROXY", str(file_config.get("http_bg_proxy_enabled", False))).strip().lower()
        http_bg_proxy_target = os.getenv("SARI_HTTP_BG_PROXY_TARGET", str(file_config.get("http_bg_proxy_target", ""))).strip()
        run_mode = "prod" if run_mode_raw == "prod" else "dev"
        try:
            retry_max = max(1, int(retry_max_raw))
        except ValueError:
            retry_max = 5
        try:
            backoff_sec = max(1, int(backoff_raw))
        except ValueError:
            backoff_sec = 1
        try:
            poll_ms = max(100, int(poll_raw))
        except ValueError:
            poll_ms = 500
        try:
            debounce_ms = max(50, int(debounce_raw))
        except ValueError:
            debounce_ms = 300
        try:
            worker_count = max(1, int(worker_raw))
        except ValueError:
            worker_count = 4
        try:
            p95_threshold_ms = max(1, int(p95_raw))
        except ValueError:
            p95_threshold_ms = 180_000
        try:
            dead_ratio_bps = max(1, int(dead_ratio_raw))
        except ValueError:
            dead_ratio_bps = 10
        try:
            alert_window_sec = max(60, int(alert_window_raw))
        except ValueError:
            alert_window_sec = 300
        try:
            auto_tick_sec = max(1, int(auto_tick_raw))
        except ValueError:
            auto_tick_sec = 5
        try:
            heartbeat_sec = max(1, int(heartbeat_raw))
        except ValueError:
            heartbeat_sec = 2
        try:
            stale_timeout_sec = max(5, int(stale_timeout_raw))
        except ValueError:
            stale_timeout_sec = 15
        try:
            lsp_request_timeout_sec = max(0.1, float(lsp_timeout_raw))
        except ValueError:
            lsp_request_timeout_sec = 20.0
        try:
            lsp_max_instances_per_repo_language = max(1, int(lsp_max_per_repo_lang_raw))
        except ValueError:
            lsp_max_instances_per_repo_language = 2
        try:
            lsp_global_soft_limit = max(0, int(lsp_global_soft_limit_raw))
        except ValueError:
            lsp_global_soft_limit = 0
        try:
            lsp_bulk_max_instances_per_repo_language = max(1, int(lsp_bulk_max_per_repo_lang_raw))
        except ValueError:
            lsp_bulk_max_instances_per_repo_language = 4
        try:
            lsp_interactive_reserved_slots_per_repo_language = max(0, int(lsp_interactive_reserved_slots_raw))
        except ValueError:
            lsp_interactive_reserved_slots_per_repo_language = 1
        try:
            lsp_interactive_timeout_sec = max(0.1, float(lsp_interactive_timeout_raw))
        except ValueError:
            lsp_interactive_timeout_sec = 2.5
        try:
            lsp_interactive_queue_max = max(16, int(lsp_interactive_queue_max_raw))
        except ValueError:
            lsp_interactive_queue_max = 256
        try:
            l3_executor_max_workers = max(0, int(l3_executor_max_workers_raw))
        except ValueError:
            l3_executor_max_workers = 0
        try:
            l3_recent_success_ttl_sec = max(0, int(l3_recent_success_ttl_raw))
        except ValueError:
            l3_recent_success_ttl_sec = 120
        try:
            l3_backpressure_cooldown_ms = max(10, int(l3_backpressure_cooldown_ms_raw))
        except ValueError:
            l3_backpressure_cooldown_ms = 300
        try:
            lsp_scale_out_hot_hits = max(2, int(lsp_scale_out_hot_hits_raw))
        except ValueError:
            lsp_scale_out_hot_hits = 24
        try:
            lsp_file_buffer_idle_ttl_sec = max(1.0, float(lsp_file_buffer_idle_ttl_raw))
        except ValueError:
            lsp_file_buffer_idle_ttl_sec = 20.0
        try:
            lsp_file_buffer_max_open = max(16, int(lsp_file_buffer_max_open_raw))
        except ValueError:
            lsp_file_buffer_max_open = 512
        try:
            lsp_java_min_major = max(8, int(lsp_java_min_major_raw))
        except ValueError:
            lsp_java_min_major = 17
        try:
            lsp_probe_timeout_default_sec = max(0.1, float(lsp_probe_timeout_default_raw))
        except ValueError:
            lsp_probe_timeout_default_sec = 20.0
        try:
            lsp_probe_timeout_go_sec = max(0.1, float(lsp_probe_timeout_go_raw))
        except ValueError:
            lsp_probe_timeout_go_sec = 45.0
        try:
            lsp_probe_workers = max(1, int(lsp_probe_workers_raw))
        except ValueError:
            lsp_probe_workers = 8
        try:
            lsp_probe_l1_workers = max(1, int(lsp_probe_l1_workers_raw))
        except ValueError:
            lsp_probe_l1_workers = 4
        try:
            lsp_probe_force_join_ms = max(0, int(lsp_probe_force_join_ms_raw))
        except ValueError:
            lsp_probe_force_join_ms = 300
        try:
            lsp_probe_warming_retry_sec = max(1, int(lsp_probe_warming_retry_sec_raw))
        except ValueError:
            lsp_probe_warming_retry_sec = 5
        try:
            lsp_probe_warming_threshold = max(1, int(lsp_probe_warming_threshold_raw))
        except ValueError:
            lsp_probe_warming_threshold = 6
        try:
            lsp_probe_permanent_backoff_sec = max(60, int(lsp_probe_permanent_backoff_sec_raw))
        except ValueError:
            lsp_probe_permanent_backoff_sec = 1800
        try:
            lsp_probe_bootstrap_file_window = max(1, int(lsp_probe_bootstrap_file_window_raw))
        except ValueError:
            lsp_probe_bootstrap_file_window = 256
        try:
            lsp_probe_bootstrap_top_k = max(1, int(lsp_probe_bootstrap_top_k_raw))
        except ValueError:
            lsp_probe_bootstrap_top_k = 3
        lsp_probe_language_priority = _parse_csv_setting(lsp_probe_language_priority_raw, cls.lsp_probe_language_priority)
        lsp_probe_l1_languages = _parse_csv_setting(lsp_probe_l1_languages_raw, cls.lsp_probe_l1_languages)
        lsp_scope_java_markers = _parse_csv_setting(lsp_scope_java_markers_raw, cls.lsp_scope_java_markers)
        lsp_scope_ts_markers = _parse_csv_setting(lsp_scope_ts_markers_raw, cls.lsp_scope_ts_markers)
        lsp_scope_vue_markers = _parse_csv_setting(lsp_scope_vue_markers_raw, cls.lsp_scope_vue_markers)
        try:
            lsp_hotness_event_window_sec = max(1.0, float(lsp_hotness_event_window_sec_raw))
        except ValueError:
            lsp_hotness_event_window_sec = cls.lsp_hotness_event_window_sec
        try:
            lsp_hotness_decay_window_sec = max(lsp_hotness_event_window_sec, float(lsp_hotness_decay_window_sec_raw))
        except ValueError:
            lsp_hotness_decay_window_sec = max(lsp_hotness_event_window_sec, cls.lsp_hotness_decay_window_sec)
        try:
            lsp_broker_backlog_min_share = min(1.0, max(0.0, float(lsp_broker_backlog_min_share_raw)))
        except ValueError:
            lsp_broker_backlog_min_share = cls.lsp_broker_backlog_min_share
        try:
            lsp_broker_max_standby_sessions_per_lang = max(0, int(lsp_broker_max_standby_sessions_per_lang_raw))
        except ValueError:
            lsp_broker_max_standby_sessions_per_lang = cls.lsp_broker_max_standby_sessions_per_lang
        try:
            lsp_broker_max_standby_sessions_per_budget_group = max(0, int(lsp_broker_max_standby_sessions_per_budget_group_raw))
        except ValueError:
            lsp_broker_max_standby_sessions_per_budget_group = cls.lsp_broker_max_standby_sessions_per_budget_group
        try:
            lsp_broker_ts_vue_active_cap = max(0, int(lsp_broker_ts_vue_active_cap_raw))
        except ValueError:
            lsp_broker_ts_vue_active_cap = cls.lsp_broker_ts_vue_active_cap
        try:
            lsp_broker_java_hot_lanes = max(0, int(lsp_broker_java_hot_lanes_raw))
            lsp_broker_java_backlog_lanes = max(0, int(lsp_broker_java_backlog_lanes_raw))
            lsp_broker_java_sticky_ttl_sec = max(0.0, float(lsp_broker_java_sticky_ttl_sec_raw))
            lsp_broker_java_switch_cooldown_sec = max(0.0, float(lsp_broker_java_switch_cooldown_sec_raw))
            lsp_broker_java_min_lease_ms = max(0, int(lsp_broker_java_min_lease_ms_raw))
        except ValueError:
            lsp_broker_java_hot_lanes = cls.lsp_broker_java_hot_lanes
            lsp_broker_java_backlog_lanes = cls.lsp_broker_java_backlog_lanes
            lsp_broker_java_sticky_ttl_sec = cls.lsp_broker_java_sticky_ttl_sec
            lsp_broker_java_switch_cooldown_sec = cls.lsp_broker_java_switch_cooldown_sec
            lsp_broker_java_min_lease_ms = cls.lsp_broker_java_min_lease_ms
        try:
            lsp_broker_ts_hot_lanes = max(0, int(lsp_broker_ts_hot_lanes_raw))
            lsp_broker_ts_backlog_lanes = max(0, int(lsp_broker_ts_backlog_lanes_raw))
            lsp_broker_ts_sticky_ttl_sec = max(0.0, float(lsp_broker_ts_sticky_ttl_sec_raw))
            lsp_broker_ts_switch_cooldown_sec = max(0.0, float(lsp_broker_ts_switch_cooldown_sec_raw))
            lsp_broker_ts_min_lease_ms = max(0, int(lsp_broker_ts_min_lease_ms_raw))
        except ValueError:
            lsp_broker_ts_hot_lanes = cls.lsp_broker_ts_hot_lanes
            lsp_broker_ts_backlog_lanes = cls.lsp_broker_ts_backlog_lanes
            lsp_broker_ts_sticky_ttl_sec = cls.lsp_broker_ts_sticky_ttl_sec
            lsp_broker_ts_switch_cooldown_sec = cls.lsp_broker_ts_switch_cooldown_sec
            lsp_broker_ts_min_lease_ms = cls.lsp_broker_ts_min_lease_ms
        try:
            lsp_broker_vue_hot_lanes = max(0, int(lsp_broker_vue_hot_lanes_raw))
            lsp_broker_vue_backlog_lanes = max(0, int(lsp_broker_vue_backlog_lanes_raw))
            lsp_broker_vue_sticky_ttl_sec = max(0.0, float(lsp_broker_vue_sticky_ttl_sec_raw))
            lsp_broker_vue_switch_cooldown_sec = max(0.0, float(lsp_broker_vue_switch_cooldown_sec_raw))
            lsp_broker_vue_min_lease_ms = max(0, int(lsp_broker_vue_min_lease_ms_raw))
        except ValueError:
            lsp_broker_vue_hot_lanes = cls.lsp_broker_vue_hot_lanes
            lsp_broker_vue_backlog_lanes = cls.lsp_broker_vue_backlog_lanes
            lsp_broker_vue_sticky_ttl_sec = cls.lsp_broker_vue_sticky_ttl_sec
            lsp_broker_vue_switch_cooldown_sec = cls.lsp_broker_vue_switch_cooldown_sec
            lsp_broker_vue_min_lease_ms = cls.lsp_broker_vue_min_lease_ms
        l3_supported_languages = _parse_csv_setting(l3_supported_languages_raw, cls.l3_supported_languages)
        try:
            lsp_max_concurrent_starts = min(4, max(1, int(lsp_max_concurrent_starts_raw)))
        except ValueError:
            lsp_max_concurrent_starts = 4
        try:
            lsp_max_concurrent_l1_probes = min(8, max(1, int(lsp_max_concurrent_l1_probes_raw)))
        except ValueError:
            lsp_max_concurrent_l1_probes = 4
        try:
            orphan_check_sec = max(1, int(orphan_check_raw))
        except ValueError:
            orphan_check_sec = 1
        try:
            shutdown_join_sec = max(1, int(shutdown_join_raw))
        except ValueError:
            shutdown_join_sec = 2
        try:
            vector_dim = max(16, int(vector_dim_raw))
        except ValueError:
            vector_dim = 128
        try:
            vector_candidate_k = max(1, int(vector_candidate_raw))
        except ValueError:
            vector_candidate_k = 50
        try:
            vector_rerank_k = max(1, int(vector_rerank_raw))
        except ValueError:
            vector_rerank_k = 20
        try:
            vector_blend_weight = max(0.0, min(1.0, float(vector_blend_raw)))
        except ValueError:
            vector_blend_weight = 0.2
        try:
            vector_min_similarity_threshold = max(0.0, min(1.0, float(vector_min_similarity_raw)))
        except ValueError:
            vector_min_similarity_threshold = 0.15
        try:
            vector_max_boost = max(0.0, min(1.0, float(vector_max_boost_raw)))
        except ValueError:
            vector_max_boost = 0.2
        try:
            vector_min_token_count_for_rerank = max(1, int(vector_min_token_raw))
        except ValueError:
            vector_min_token_count_for_rerank = 2
        try:
            importance_max_boost = max(0.0, float(importance_max_boost_raw))
        except ValueError:
            importance_max_boost = 200.0
        try:
            ranking_w_rrf = max(0.0, min(1.0, float(ranking_w_rrf_raw)))
        except ValueError:
            ranking_w_rrf = 0.55
        try:
            ranking_w_importance = max(0.0, min(1.0, float(ranking_w_importance_raw)))
        except ValueError:
            ranking_w_importance = 0.30
        try:
            ranking_w_vector = max(0.0, min(1.0, float(ranking_w_vector_raw)))
        except ValueError:
            ranking_w_vector = 0.15
        try:
            ranking_w_hierarchy = max(0.0, min(1.0, float(ranking_w_hierarchy_raw)))
        except ValueError:
            ranking_w_hierarchy = 0.15
        try:
            mcp_daemon_timeout_sec = max(0.1, float(mcp_daemon_timeout_raw))
        except ValueError:
            mcp_daemon_timeout_sec = 2.0
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
        return cls(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=50,
            stop_grace_sec=10,
            candidate_backend=backend,
            candidate_fallback_scan=(fallback_flag != "0"),
            pipeline_retry_max=retry_max,
            pipeline_backoff_base_sec=backoff_sec,
            queue_poll_interval_ms=poll_ms,
            watcher_debounce_ms=debounce_ms,
            collection_include_ext=include_ext,
            collection_exclude_globs=exclude_globs,
            pipeline_worker_count=worker_count,
            pipeline_l3_p95_threshold_ms=p95_threshold_ms,
            pipeline_dead_ratio_threshold_bps=dead_ratio_bps,
            pipeline_alert_window_sec=alert_window_sec,
            pipeline_auto_tick_interval_sec=auto_tick_sec,
            l3_parallel_enabled=l3_parallel_enabled_raw in {"1", "true", "yes", "on"},
            run_mode=run_mode,
            daemon_heartbeat_interval_sec=heartbeat_sec,
            daemon_stale_timeout_sec=stale_timeout_sec,
            lsp_request_timeout_sec=lsp_request_timeout_sec,
            lsp_max_instances_per_repo_language=lsp_max_instances_per_repo_language,
            lsp_bulk_mode_enabled=lsp_bulk_mode_enabled_raw in {"1", "true", "yes", "on"},
            lsp_bulk_max_instances_per_repo_language=lsp_bulk_max_instances_per_repo_language,
            lsp_interactive_reserved_slots_per_repo_language=lsp_interactive_reserved_slots_per_repo_language,
            lsp_interactive_timeout_sec=lsp_interactive_timeout_sec,
            lsp_interactive_queue_max=lsp_interactive_queue_max,
            lsp_global_soft_limit=lsp_global_soft_limit,
            lsp_scale_out_hot_hits=lsp_scale_out_hot_hits,
            l3_executor_max_workers=l3_executor_max_workers,
            l3_recent_success_ttl_sec=l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=l3_backpressure_on_interactive_raw in {"1", "true", "yes", "on"},
            l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms,
            l3_supported_languages=l3_supported_languages,
            lsp_file_buffer_idle_ttl_sec=lsp_file_buffer_idle_ttl_sec,
            lsp_file_buffer_max_open=lsp_file_buffer_max_open,
            lsp_java_min_major=lsp_java_min_major,
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
            lsp_scope_planner_enabled=lsp_scope_planner_enabled_raw in {"1", "true", "yes", "on"},
            lsp_scope_planner_shadow_mode=lsp_scope_planner_shadow_mode_raw in {"1", "true", "yes", "on"},
            lsp_scope_java_markers=lsp_scope_java_markers,
            lsp_scope_ts_markers=lsp_scope_ts_markers,
            lsp_scope_vue_markers=lsp_scope_vue_markers,
            lsp_scope_top_level_fallback=lsp_scope_top_level_fallback_raw in {"1", "true", "yes", "on"},
            lsp_session_broker_enabled=lsp_session_broker_enabled_raw in {"1", "true", "yes", "on"},
            lsp_session_broker_metrics_enabled=lsp_session_broker_metrics_enabled_raw in {"1", "true", "yes", "on"},
            lsp_broker_optional_scaffolding_enabled=lsp_broker_optional_scaffolding_enabled_raw in {"1", "true", "yes", "on"},
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
            lsp_max_concurrent_starts=lsp_max_concurrent_starts,
            lsp_max_concurrent_l1_probes=lsp_max_concurrent_l1_probes,
            orphan_ppid_check_interval_sec=orphan_check_sec,
            shutdown_join_timeout_sec=shutdown_join_sec,
            importance_normalize_mode=normalized_mode,
            importance_max_boost=importance_max_boost,
            importance_core_path_tokens=importance_core_path_tokens,
            importance_noisy_path_tokens=importance_noisy_path_tokens,
            importance_code_extensions=importance_code_extensions,
            importance_noisy_extensions=importance_noisy_extensions,
            vector_enabled=vector_enabled_raw in {"1", "true", "yes", "on"},
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
            mcp_forward_to_daemon=mcp_forward_to_daemon_raw in {"1", "true", "yes", "on"},
            mcp_daemon_autostart=mcp_daemon_autostart_raw in {"1", "true", "yes", "on"},
            mcp_daemon_timeout_sec=mcp_daemon_timeout_sec,
            strict_protocol=strict_protocol_raw in {"1", "true", "yes", "on"},
            stabilization_enabled=stabilization_enabled_raw not in {"0", "false", "no", "off"},
            http_bg_proxy_enabled=http_bg_proxy_enabled_raw in {"1", "true", "yes", "on"},
            http_bg_proxy_target=http_bg_proxy_target,
        )


def _load_user_config() -> dict[str, object]:
    """사용자 설정 파일을 읽어 딕셔너리로 반환한다."""
    config_path = Path.home() / ".sari" / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
