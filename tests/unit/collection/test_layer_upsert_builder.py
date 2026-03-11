from __future__ import annotations

from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason
from sari.services.collection.layer_upsert_builder import LayerUpsertBuilder
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessDecision, L3PreprocessResultDTO


def test_layer_upsert_builder_builds_l3_l4_l5_payloads() -> None:
    builder = LayerUpsertBuilder()
    preprocess = L3PreprocessResultDTO(
        symbols=[{"name": "A", "kind": "class", "line": 1}],
        degraded=False,
        decision=L3PreprocessDecision.L3_ONLY,
        source="tree_sitter",
        reason="ok",
    )
    admission = L4AdmissionDecisionDTO(
        admit_l5=False,
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
    )

    l3 = builder.build_l3(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=preprocess,
        now_iso="2026-01-01T00:00:00Z",
    )
    l4 = builder.build_l4(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        preprocess_result=preprocess,
        admission_decision=admission,
        now_iso="2026-01-01T00:00:00Z",
    )
    l5 = builder.build_l5(
        repo_root="/repo",
        relative_path="src/a.py",
        content_hash="h1",
        reason_code=L5ReasonCode.GOLDENSET_COVERAGE,
        symbols=[{"name": "A"}],
        relations=[{"from": "A", "to": "B"}],
        now_iso="2026-01-01T00:00:00Z",
    )

    assert l3["workspace_id"] == "/repo"
    assert l3["degraded"] is False
    assert l3["l3_skipped_large_file"] is False
    assert l4["confidence"] == 0.9  # L3_ONLY + not degraded → confidence 0.9
    assert l4["normalized"]["admit_l5"] is False
    assert l4["normalized"]["reject_reason"] == "pressure_rate_exceeded"
    assert l5["reason_code"] == L5ReasonCode.GOLDENSET_COVERAGE.value
    assert l5["semantics"]["symbols_count"] == 1
    assert l5["semantics"]["relations_count"] == 1
    assert l5["semantics"]["zero_relations_retry_pending"] is False
