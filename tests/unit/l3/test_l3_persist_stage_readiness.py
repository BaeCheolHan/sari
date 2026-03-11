from __future__ import annotations

from dataclasses import dataclass

from sari.core.models import L5ReasonCode
from sari.services.collection.l3.l3_job_context import L3JobContext
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3PreprocessResultDTO,
)
from sari.services.collection.l3.stages.persist_stage import L3PersistStage


@dataclass
class _LayerBuilder:
    def build_l3(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        return {"layer": "l3"}

    def build_l4(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        return {"layer": "l4"}

    def build_l5(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        return {"layer": "l5"}


def _preprocess() -> L3PreprocessResultDTO:
    return L3PreprocessResultDTO(
        symbols=[{"name": "run_stdio_proxy", "kind": "function", "line": 10, "end_line": 20}],
        degraded=False,
        decision=L3PreprocessDecision.NEEDS_L5,
        source="tree_sitter_outline",
        reason="needs_l5",
    )


def test_apply_l5_success_sets_get_callers_ready_false_when_relations_empty() -> None:
    stage = L3PersistStage(layer_upsert_builder=_LayerBuilder(), deletion_hold_enabled=lambda: False)
    context = L3JobContext()

    stage.apply_l5_success(
        context=context,
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=_preprocess(),
        admission_decision=None,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        lsp_symbols=[{"name": "run_stdio_proxy", "kind": "function", "line": 10, "end_line": 20}],
        lsp_relations=[],
        now_iso="2026-03-03T00:00:00Z",
    )

    assert context.readiness_update is not None
    assert context.readiness_update.get_callers_ready is False
    assert context.readiness_update.last_reason == "ok"


def test_apply_l5_success_marks_retry_pending_reason_when_requested() -> None:
    stage = L3PersistStage(layer_upsert_builder=_LayerBuilder(), deletion_hold_enabled=lambda: False)
    context = L3JobContext()

    stage.apply_l5_success(
        context=context,
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=_preprocess(),
        admission_decision=None,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        lsp_symbols=[{"name": "run_stdio_proxy", "kind": "function", "line": 10, "end_line": 20}],
        lsp_relations=[],
        now_iso="2026-03-03T00:00:00Z",
        retry_zero_relations_pending=True,
    )

    assert context.readiness_update is not None
    assert context.readiness_update.last_reason == "ok_zero_relations_retry_pending"
    assert context.readiness_update.tool_ready is False
    assert context.state_update is not None
    assert context.state_update.enrich_state == "LSP_READY"


def test_apply_l5_success_sets_repo_id_on_lsp_update() -> None:
    stage = L3PersistStage(layer_upsert_builder=_LayerBuilder(), deletion_hold_enabled=lambda: False)
    context = L3JobContext()

    stage.apply_l5_success(
        context=context,
        repo_id="r1",
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=_preprocess(),
        admission_decision=None,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        lsp_symbols=[{"name": "run_stdio_proxy", "kind": "function", "line": 10, "end_line": 20}],
        lsp_relations=[],
        now_iso="2026-03-03T00:00:00Z",
    )

    assert context.lsp_update is not None
    assert context.lsp_update.repo_id == "r1"


def test_apply_l5_success_sets_get_callers_ready_true_when_relations_present() -> None:
    stage = L3PersistStage(layer_upsert_builder=_LayerBuilder(), deletion_hold_enabled=lambda: False)
    context = L3JobContext()

    stage.apply_l5_success(
        context=context,
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=_preprocess(),
        admission_decision=None,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        lsp_symbols=[{"name": "run_stdio_proxy", "kind": "function", "line": 10, "end_line": 20}],
        lsp_relations=[{"from_symbol": "A", "to_symbol": "B", "line": 10}],
        now_iso="2026-03-03T00:00:00Z",
    )

    assert context.readiness_update is not None
    assert context.readiness_update.get_callers_ready is True


def test_apply_l3_only_success_sets_get_callers_ready_false() -> None:
    stage = L3PersistStage(layer_upsert_builder=_LayerBuilder(), deletion_hold_enabled=lambda: False)
    context = L3JobContext()

    stage.apply_l3_only_success(
        context=context,
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=_preprocess(),
        admission_decision=None,
        now_iso="2026-03-03T00:00:00Z",
    )

    assert context.readiness_update is not None
    assert context.readiness_update.get_callers_ready is False
