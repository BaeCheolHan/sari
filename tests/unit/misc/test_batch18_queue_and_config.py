"""Batch-18 큐 우선순위/설정 외부화 동작을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sari.core.config import AppConfig
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.schema import init_schema


def test_file_enrich_queue_acquire_orders_by_priority(tmp_path: Path) -> None:
    """acquire_pending은 우선순위 높은 작업을 먼저 할당해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo = FileEnrichQueueRepository(db_path)

    now_iso = "2026-02-16T00:00:00+00:00"
    repo.enqueue("/repo", "low.py", "h-low", 30, "scan", now_iso)
    repo.enqueue("/repo", "high.py", "h-high", 90, "watcher", now_iso)
    repo.enqueue("/repo", "mid.py", "h-mid", 60, "manual", now_iso)

    jobs = repo.acquire_pending(limit=3, now_iso=now_iso)

    assert len(jobs) == 3
    assert jobs[0].relative_path == "high.py"
    assert jobs[1].relative_path == "mid.py"
    assert jobs[2].relative_path == "low.py"


def test_app_config_loads_json_and_env_override(tmp_path: Path, monkeypatch) -> None:
    """설정 파일 값을 읽되 env 값이 우선 적용되어야 한다."""
    home = tmp_path / "home"
    config_dir = home / ".sari"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "watcher_debounce_ms": 450,
                "collection_include_ext": [".py", ".cpp"],
                "collection_exclude_globs": ["**/.git/**", "**/vendor/**"],
                "importance_normalize_mode": "minmax",
                "importance_max_boost": 77.0,
                "importance_core_path_tokens": ["src", "domain"],
                "importance_noisy_path_tokens": ["test", "fixtures"],
                "importance_code_extensions": [".py", ".rb"],
                "importance_noisy_extensions": [".lock", ".snap"],
                "vector_min_similarity_threshold": 0.33,
                "vector_max_boost": 0.12,
                "vector_min_token_count_for_rerank": 4,
                "vector_apply_to_item_types": ["symbol"],
                "ranking_w_rrf": 0.6,
                "ranking_w_importance": 0.3,
                "ranking_w_vector": 0.1,
                "ranking_w_hierarchy": 0.2,
                "l3_parallel_enabled": False,
                "lsp_file_buffer_idle_ttl_sec": 15.0,
                "lsp_file_buffer_max_open": 256,
                "lsp_java_min_major": 19,
                "lsp_probe_timeout_default_sec": 18.0,
                "lsp_probe_timeout_go_sec": 40.0,
                "lsp_probe_workers": 3,
                "lsp_probe_force_join_ms": 250,
                "lsp_probe_warming_retry_sec": 7,
                "lsp_probe_warming_threshold": 9,
                "lsp_probe_permanent_backoff_sec": 2400,
                "lsp_probe_l1_languages": ["go", "java"],
                "l3_supported_languages": ["go", "java", "python"],
                "lsp_max_concurrent_starts": 1,
                "lsp_max_concurrent_l1_probes": 3,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SARI_WATCHER_DEBOUNCE_MS", "700")
    monkeypatch.setenv("SARI_IMPORTANCE_MAX_BOOST", "88")
    monkeypatch.setenv("SARI_IMPORTANCE_CORE_PATH_TOKENS", "src,application")
    monkeypatch.setenv("SARI_VECTOR_MIN_SIMILARITY_THRESHOLD", "0.5")
    monkeypatch.setenv("SARI_VECTOR_APPLY_TO_ITEM_TYPES", "symbol,file")
    monkeypatch.setenv("SARI_RANKING_W_RRF", "0.7")
    monkeypatch.setenv("SARI_RANKING_W_IMPORTANCE", "0.2")
    monkeypatch.setenv("SARI_RANKING_W_VECTOR", "0.1")
    monkeypatch.setenv("SARI_RANKING_W_HIERARCHY", "0.1")
    monkeypatch.setenv("SARI_LSP_REQUEST_TIMEOUT_SEC", "12.5")
    monkeypatch.setenv("SARI_LSP_SYMBOL_INFO_BUDGET_SEC", "6.5")
    monkeypatch.setenv("SARI_LSP_INCLUDE_INFO_DEFAULT", "1")
    monkeypatch.setenv("SARI_L3_PARALLEL_ENABLED", "true")
    monkeypatch.setenv("SARI_LSP_FILE_BUFFER_IDLE_TTL_SEC", "25.0")
    monkeypatch.setenv("SARI_LSP_FILE_BUFFER_MAX_OPEN", "1024")
    monkeypatch.setenv("SARI_LSP_JAVA_MIN_MAJOR", "21")
    monkeypatch.setenv("SARI_LSP_PROBE_TIMEOUT_DEFAULT_SEC", "22.5")
    monkeypatch.setenv("SARI_LSP_PROBE_TIMEOUT_GO_SEC", "55")
    monkeypatch.setenv("SARI_LSP_PROBE_WORKERS", "5")
    monkeypatch.setenv("SARI_LSP_PROBE_FORCE_JOIN_MS", "400")
    monkeypatch.setenv("SARI_LSP_PROBE_WARMING_RETRY_SEC", "8")
    monkeypatch.setenv("SARI_LSP_PROBE_WARMING_THRESHOLD", "10")
    monkeypatch.setenv("SARI_LSP_PROBE_PERMANENT_BACKOFF_SEC", "3600")
    monkeypatch.setenv("SARI_LSP_PROBE_L1_LANGUAGES", "go,kotlin")
    monkeypatch.setenv("SARI_L3_SUPPORTED_LANGUAGES", "go,kotlin")
    monkeypatch.setenv("SARI_LSP_MAX_CONCURRENT_STARTS", "2")
    monkeypatch.setenv("SARI_LSP_MAX_CONCURRENT_L1_PROBES", "4")
    monkeypatch.setenv("SARI_LSP_SCOPE_JAVA_MARKERS", "pom.xml,build.gradle.kts")
    monkeypatch.setenv("SARI_LSP_SCOPE_TS_MARKERS", "package.json,tsconfig.json")
    monkeypatch.setenv("SARI_LSP_SCOPE_VUE_MARKERS", "package.json,vue.config.js")
    monkeypatch.setenv("SARI_LSP_SCOPE_TOP_LEVEL_FALLBACK", "1")
    monkeypatch.setenv("SARI_LSP_SCOPE_ACTIVE_LANGUAGES", "java")
    monkeypatch.setenv("SARI_LSP_SESSION_BROKER_ENABLED", "1")
    monkeypatch.setenv("SARI_LSP_HOTNESS_EVENT_WINDOW_SEC", "12")
    monkeypatch.setenv("SARI_LSP_HOTNESS_DECAY_WINDOW_SEC", "45")
    monkeypatch.setenv("SARI_LSP_BROKER_BACKLOG_MIN_SHARE", "0.25")
    monkeypatch.setenv("SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_LANG", "3")
    monkeypatch.setenv("SARI_LSP_BROKER_MAX_STANDBY_SESSIONS_PER_BUDGET_GROUP", "4")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_VUE_ACTIVE_CAP", "3")
    monkeypatch.setenv("SARI_LSP_BROKER_JAVA_HOT_LANES", "2")
    monkeypatch.setenv("SARI_LSP_BROKER_JAVA_BACKLOG_LANES", "1")
    monkeypatch.setenv("SARI_LSP_BROKER_JAVA_STICKY_TTL_SEC", "700")
    monkeypatch.setenv("SARI_LSP_BROKER_JAVA_SWITCH_COOLDOWN_SEC", "6")
    monkeypatch.setenv("SARI_LSP_BROKER_JAVA_MIN_LEASE_MS", "1800")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_HOT_LANES", "1")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_BACKLOG_LANES", "2")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_STICKY_TTL_SEC", "200")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_SWITCH_COOLDOWN_SEC", "3")
    monkeypatch.setenv("SARI_LSP_BROKER_TS_MIN_LEASE_MS", "650")
    monkeypatch.setenv("SARI_LSP_BROKER_VUE_HOT_LANES", "1")
    monkeypatch.setenv("SARI_LSP_BROKER_VUE_BACKLOG_LANES", "1")
    monkeypatch.setenv("SARI_LSP_BROKER_VUE_STICKY_TTL_SEC", "260")
    monkeypatch.setenv("SARI_LSP_BROKER_VUE_SWITCH_COOLDOWN_SEC", "4")
    monkeypatch.setenv("SARI_LSP_BROKER_VUE_MIN_LEASE_MS", "900")
    monkeypatch.setenv("SARI_L3_TREE_SITTER_EXECUTOR_MODE", "subinterp")
    monkeypatch.setenv("SARI_L3_TREE_SITTER_SUBINTERP_WORKERS", "6")
    monkeypatch.setenv("SARI_L3_TREE_SITTER_SUBINTERP_MIN_BYTES", "8192")
    monkeypatch.setenv("SARI_L5_SYMBOL_NORMALIZER_EXECUTOR_MODE", "subinterp")
    monkeypatch.setenv("SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_WORKERS", "3")
    monkeypatch.setenv("SARI_L5_SYMBOL_NORMALIZER_SUBINTERP_MIN_SYMBOLS", "250")
    monkeypatch.delenv("SARI_COLLECTION_INCLUDE_EXT", raising=False)
    monkeypatch.delenv("SARI_COLLECTION_EXCLUDE_GLOBS", raising=False)

    config = AppConfig.default()

    assert config.watcher_debounce_ms == 700
    assert config.collection_include_ext == (".py", ".cpp")
    assert config.collection_exclude_globs == ("**/.git/**", "**/vendor/**")
    assert config.importance_normalize_mode == "minmax"
    assert config.importance_max_boost == 88.0
    assert config.importance_core_path_tokens == ("src", "application")
    assert config.importance_noisy_path_tokens == ("test", "fixtures")
    assert config.importance_code_extensions == (".py", ".rb")
    assert config.importance_noisy_extensions == (".lock", ".snap")
    assert config.vector_min_similarity_threshold == 0.5
    assert config.vector_max_boost == 0.12
    assert config.vector_min_token_count_for_rerank == 4
    assert config.vector_apply_to_item_types == ("symbol", "file")
    assert config.ranking_w_rrf == pytest.approx(0.6363636363)
    assert config.ranking_w_importance == pytest.approx(0.1818181818)
    assert config.ranking_w_vector == pytest.approx(0.0909090909)
    assert config.ranking_w_hierarchy == pytest.approx(0.0909090909)
    assert config.lsp_request_timeout_sec == pytest.approx(12.5)
    assert config.lsp_symbol_info_budget_sec == pytest.approx(6.5)
    assert config.lsp_include_info_default is True
    assert config.l3_parallel_enabled is True
    assert config.lsp_file_buffer_idle_ttl_sec == pytest.approx(25.0)
    assert config.lsp_file_buffer_max_open == 1024
    assert config.lsp_java_min_major == 21
    assert config.lsp_probe_timeout_default_sec == pytest.approx(22.5)
    assert config.lsp_probe_timeout_go_sec == pytest.approx(55.0)
    assert config.lsp_probe_workers == 5
    assert config.lsp_probe_force_join_ms == 400
    assert config.lsp_probe_warming_retry_sec == 8
    assert config.lsp_probe_warming_threshold == 10
    assert config.lsp_probe_permanent_backoff_sec == 3600
    assert config.lsp_probe_l1_languages == ("go", "kotlin")
    assert config.l3_supported_languages == ("go", "kotlin")
    assert config.lsp_max_concurrent_starts == 2
    assert config.lsp_max_concurrent_l1_probes == 4
    assert config.lsp_scope_java_markers == ("pom.xml", "build.gradle.kts")
    assert config.lsp_scope_ts_markers == ("package.json", "tsconfig.json")
    assert config.lsp_scope_vue_markers == ("package.json", "vue.config.js")
    assert config.lsp_scope_top_level_fallback is True
    assert config.lsp_scope_active_languages == ("java",)
    assert config.lsp_session_broker_enabled is True
    assert config.lsp_hotness_event_window_sec == pytest.approx(12.0)
    assert config.lsp_hotness_decay_window_sec == pytest.approx(45.0)
    assert config.lsp_broker_backlog_min_share == pytest.approx(0.25)
    assert config.lsp_broker_max_standby_sessions_per_lang == 3
    assert config.lsp_broker_max_standby_sessions_per_budget_group == 4
    assert config.lsp_broker_ts_vue_active_cap == 3
    assert config.lsp_broker_java_hot_lanes == 2
    assert config.lsp_broker_java_backlog_lanes == 1
    assert config.lsp_broker_java_sticky_ttl_sec == pytest.approx(700.0)
    assert config.lsp_broker_java_switch_cooldown_sec == pytest.approx(6.0)
    assert config.lsp_broker_java_min_lease_ms == 1800
    assert config.lsp_broker_ts_hot_lanes == 1
    assert config.lsp_broker_ts_backlog_lanes == 2
    assert config.lsp_broker_ts_sticky_ttl_sec == pytest.approx(200.0)
    assert config.lsp_broker_ts_switch_cooldown_sec == pytest.approx(3.0)
    assert config.lsp_broker_ts_min_lease_ms == 650
    assert config.lsp_broker_vue_hot_lanes == 1
    assert config.lsp_broker_vue_backlog_lanes == 1
    assert config.lsp_broker_vue_sticky_ttl_sec == pytest.approx(260.0)
    assert config.lsp_broker_vue_switch_cooldown_sec == pytest.approx(4.0)
    assert config.lsp_broker_vue_min_lease_ms == 900
    assert config.l3_tree_sitter_executor_mode == "subinterp"
    assert config.l3_tree_sitter_subinterp_workers == 6
    assert config.l3_tree_sitter_subinterp_min_bytes == 8192
    assert config.l5_symbol_normalizer_executor_mode == "subinterp"
    assert config.l5_symbol_normalizer_subinterp_workers == 3
    assert config.l5_symbol_normalizer_subinterp_min_symbols == 250


def test_app_config_default_exclude_globs_include_build_artifact_paths(tmp_path: Path, monkeypatch) -> None:
    """기본 exclude 목록은 빌드 산출물 경로를 폭넓게 포함해야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SARI_COLLECTION_EXCLUDE_GLOBS", raising=False)
    monkeypatch.delenv("SARI_LSP_PROBE_WORKERS", raising=False)
    monkeypatch.delenv("SARI_LSP_PROBE_L1_WORKERS", raising=False)
    monkeypatch.delenv("SARI_LSP_PROBE_L1_LANGUAGES", raising=False)
    monkeypatch.delenv("SARI_LSP_MAX_CONCURRENT_STARTS", raising=False)
    monkeypatch.delenv("SARI_LSP_MAX_CONCURRENT_L1_PROBES", raising=False)
    monkeypatch.delenv("SARI_RUN_MODE", raising=False)

    config = AppConfig.default()

    assert "**/.git/**" in config.collection_exclude_globs
    assert "**/node_modules/**" in config.collection_exclude_globs
    assert "**/target/**" in config.collection_exclude_globs
    assert "**/.venv/**" in config.collection_exclude_globs
    assert "**/.idea/**" in config.collection_exclude_globs
    assert "**/.cache/**" in config.collection_exclude_globs
    assert config.lsp_probe_workers == 8
    assert config.lsp_probe_l1_workers == 4
    assert config.lsp_probe_l1_languages == ("go", "java", "kotlin", "py", "rs", "ts", "js")
    assert config.lsp_max_concurrent_starts == 4
    assert config.lsp_max_concurrent_l1_probes == 4
    assert config.run_mode == "prod"


def test_app_config_defaults_for_interactive_timeout_and_instance_cap(tmp_path: Path, monkeypatch) -> None:
    """LSP 병목 완화 기본값이 반영되어야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SARI_LSP_INTERACTIVE_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("SARI_LSP_MAX_INSTANCES_PER_REPO_LANGUAGE", raising=False)

    config = AppConfig.default()

    assert config.lsp_interactive_timeout_sec == pytest.approx(4.0)
    assert config.lsp_max_instances_per_repo_language == 3


def test_app_config_search_lsp_guard_and_recent_failure_cooldown_env(tmp_path: Path, monkeypatch) -> None:
    """검색 LSP 압력 가드/실패 쿨다운 설정이 환경변수로 주입되어야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SARI_SEARCH_LSP_PRESSURE_GUARD_ENABLED", "true")
    monkeypatch.setenv("SARI_SEARCH_LSP_PRESSURE_PENDING_THRESHOLD", "2")
    monkeypatch.setenv("SARI_SEARCH_LSP_PRESSURE_TIMEOUT_THRESHOLD", "3")
    monkeypatch.setenv("SARI_SEARCH_LSP_PRESSURE_REJECTED_THRESHOLD", "4")
    monkeypatch.setenv("SARI_SEARCH_LSP_RECENT_FAILURE_COOLDOWN_SEC", "9.5")

    config = AppConfig.default()

    assert config.search_lsp_pressure_guard_enabled is True
    assert config.search_lsp_pressure_pending_threshold == 2
    assert config.search_lsp_pressure_timeout_threshold == 3
    assert config.search_lsp_pressure_rejected_threshold == 4
    assert config.search_lsp_recent_failure_cooldown_sec == pytest.approx(9.5)


def test_app_config_parses_l5_budget_and_l3_query_budget_env(tmp_path: Path, monkeypatch) -> None:
    """L5 rate/burst 예산 및 L3 query budget 설정을 파싱해야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SARI_L5_CALL_RATE_TOTAL_MAX", "0.2")
    monkeypatch.setenv("SARI_L5_CALL_RATE_BATCH_MAX", "0.03")
    monkeypatch.setenv("SARI_L5_CALLS_PER_MIN_PER_LANG_MAX", "45")
    monkeypatch.setenv("SARI_L5_TOKENS_PER_10SEC_GLOBAL_MAX", "200")
    monkeypatch.setenv("SARI_L5_TOKENS_PER_10SEC_PER_LANG_MAX", "40")
    monkeypatch.setenv("SARI_L5_TOKENS_PER_10SEC_PER_WORKSPACE_MAX", "25")
    monkeypatch.setenv("SARI_L3_QUERY_COMPILE_MS_BUDGET", "12.5")
    monkeypatch.setenv("SARI_L3_QUERY_BUDGET_MS", "33.0")

    config = AppConfig.default()

    assert config.l5_call_rate_total_max == pytest.approx(0.2)
    assert config.l5_call_rate_batch_max == pytest.approx(0.03)
    assert config.l5_calls_per_min_per_lang_max == 45
    assert config.l5_tokens_per_10sec_global_max == 200
    assert config.l5_tokens_per_10sec_per_lang_max == 40
    assert config.l5_tokens_per_10sec_per_workspace_max == 25
    assert config.l3_query_compile_ms_budget == pytest.approx(12.5)
    assert config.l3_query_budget_ms == pytest.approx(33.0)


def test_app_config_parses_mcp_tool_call_timeout_env(tmp_path: Path, monkeypatch) -> None:
    """MCP search/read call timeout 설정이 환경변수에서 파싱되어야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    config = AppConfig.default()



def test_app_config_candidate_backend_and_fallback_source_priority(tmp_path: Path, monkeypatch) -> None:
    """backend/fallback/db_path는 file 기본 + env override 규칙을 지켜야 한다."""
    home = tmp_path / "home"
    config_dir = home / ".sari"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "candidate_backend": "scan",
                "candidate_fallback_scan": 0,
                "db_path": str(home / "from-file.db"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SARI_CANDIDATE_BACKEND", raising=False)
    monkeypatch.delenv("SARI_CANDIDATE_FALLBACK_SCAN", raising=False)
    monkeypatch.delenv("SARI_DB_PATH", raising=False)

    config = AppConfig.default()
    assert config.candidate_backend == "scan"
    assert config.candidate_fallback_scan is False
    assert str(config.db_path).endswith("from-file.db")

    # env override + invalid backend fallback
    monkeypatch.setenv("SARI_CANDIDATE_BACKEND", "invalid")
    monkeypatch.setenv("SARI_CANDIDATE_FALLBACK_SCAN", "1")
    monkeypatch.setenv("SARI_DB_PATH", str(home / "from-env.db"))

    overridden = AppConfig.default()
    assert overridden.candidate_backend == "tantivy"
    assert overridden.candidate_fallback_scan is True
    assert str(overridden.db_path).endswith("from-env.db")


def test_app_config_release_mode_limits_env_surface(tmp_path: Path, monkeypatch) -> None:
    """release 모드는 allowlist 밖 env를 무시하고 file/default를 사용해야 한다."""
    home = tmp_path / "home"
    config_dir = home / ".sari"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "run_mode": "release",
                "lsp_probe_workers": 3,
                "collection_include_ext": [".py", ".kt"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SARI_RUN_MODE", "release")
    # allowlist 밖 설정: release에서는 무시되어야 한다.
    monkeypatch.setenv("SARI_LSP_PROBE_WORKERS", "99")
    # allowlist 안 설정: release에서도 override 허용된다.
    monkeypatch.setenv("SARI_COLLECTION_INCLUDE_EXT", ".py,.java")

    config = AppConfig.default()

    assert config.run_mode == "prod"
    assert config.lsp_probe_workers == 3
    assert config.collection_include_ext == (".py", ".java")


def test_app_config_test_mode_allows_env_override(tmp_path: Path, monkeypatch) -> None:
    """test 모드는 기존처럼 env override를 허용해야 한다."""
    home = tmp_path / "home"
    config_dir = home / ".sari"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "run_mode": "test",
                "lsp_probe_workers": 3,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SARI_RUN_MODE", "test")
    monkeypatch.setenv("SARI_LSP_PROBE_WORKERS", "77")

    config = AppConfig.default()

    assert config.run_mode == "dev"
    assert config.lsp_probe_workers == 77
