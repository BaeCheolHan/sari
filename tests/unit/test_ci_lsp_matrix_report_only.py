"""LSP 매트릭스 CI report-only 구성을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_ci_script_supports_report_only_and_diagnose_artifacts() -> None:
    """run_lsp_matrix_gate 스크립트는 report-only/diagnose 산출을 지원해야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "run_lsp_matrix_gate.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "--report-only" in content
    assert "lsp-matrix-diagnose.json" in content
    assert "lsp-matrix-diagnose.md" in content
    assert "pipeline lsp-matrix diagnose" in content
    assert "repair_missing_servers.sh" in content
    assert "lsp-matrix-gate-summary.json" in content
    assert "--apply" in content
    assert "SARI_LSP_MATRIX_GATE_TIMEOUT_SEC" in content
    assert "subprocess.run" in content
    assert "timeout=" in content


def test_ci_workflow_defaults_to_report_only_mode() -> None:
    """PR 워크플로우는 기본 report-only 모드로 스크립트를 호출해야 한다."""
    root = Path(__file__).resolve().parents[2]
    workflow_path = root / ".github" / "workflows" / "lsp-matrix-pr-gate.yml"
    content = workflow_path.read_text(encoding="utf-8")

    assert "report_only" in content
    assert "--report-only" in content
    assert "lsp-matrix-diagnose.json" in content
    assert "lsp-matrix-diagnose.md" in content
    assert "push:" in content
    assert "schedule:" in content
