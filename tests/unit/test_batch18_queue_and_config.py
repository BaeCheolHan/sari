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
    monkeypatch.setenv("SARI_L3_PARALLEL_ENABLED", "true")
    monkeypatch.setenv("SARI_LSP_FILE_BUFFER_IDLE_TTL_SEC", "25.0")
    monkeypatch.setenv("SARI_LSP_FILE_BUFFER_MAX_OPEN", "1024")
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
    assert config.l3_parallel_enabled is True
    assert config.lsp_file_buffer_idle_ttl_sec == pytest.approx(25.0)
    assert config.lsp_file_buffer_max_open == 1024


def test_app_config_default_exclude_globs_include_build_artifact_paths(tmp_path: Path, monkeypatch) -> None:
    """기본 exclude 목록은 빌드 산출물 경로를 폭넓게 포함해야 한다."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SARI_COLLECTION_EXCLUDE_GLOBS", raising=False)
    monkeypatch.delenv("SARI_RUN_MODE", raising=False)

    config = AppConfig.default()

    assert "**/.git/**" in config.collection_exclude_globs
    assert "**/node_modules/**" in config.collection_exclude_globs
    assert "**/target/**" in config.collection_exclude_globs
    assert "**/.venv/**" in config.collection_exclude_globs
    assert "**/.idea/**" in config.collection_exclude_globs
    assert "**/.cache/**" in config.collection_exclude_globs
    assert config.run_mode == "prod"
