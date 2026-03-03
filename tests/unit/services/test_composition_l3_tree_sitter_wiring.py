"""Composition 경로의 L3 tree-sitter executor 설정 전달을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from sari.core import composition as composition_module
from sari.core.config import AppConfig


def test_build_file_collection_service_from_config_wires_l3_tree_sitter_settings(monkeypatch) -> None:
    """AppConfig의 l3_tree_sitter_* 값이 런타임 빌더로 전달되어야 한다."""

    config = replace(
        AppConfig.default(),
        l3_tree_sitter_executor_mode="subinterp",
        l3_tree_sitter_subinterp_workers=7,
        l3_tree_sitter_subinterp_min_bytes=12_288,
        pipeline_l5_worker_count=3,
    )

    captured: dict[str, object] = {}
    sentinel = object()

    def _fake_build_default_file_collection_service(**kwargs):
        captured.update(kwargs)
        return sentinel

    import sari.services.collection.service as collection_service_module

    monkeypatch.setattr(
        collection_service_module,
        "build_default_file_collection_service",
        _fake_build_default_file_collection_service,
    )

    repos = SimpleNamespace(
        workspace_repo=object(),
        file_repo=object(),
        enrich_queue_repo=object(),
        body_repo=object(),
        lsp_repo=object(),
        readiness_repo=object(),
        policy_repo=object(),
        event_repo=object(),
        error_event_repo=object(),
        tool_layer_repo=object(),
    )

    result = composition_module.build_file_collection_service_from_config(
        config=config,
        repos=repos,
        lsp_backend=object(),
        run_mode="prod",
    )

    assert result is sentinel
    assert captured["l3_tree_sitter_executor_mode"] == "subinterp"
    assert captured["l3_tree_sitter_subinterp_workers"] == 7
    assert captured["l3_tree_sitter_subinterp_min_bytes"] == 12_288
    assert captured["pipeline_l5_worker_count"] == 3
