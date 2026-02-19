"""Release gate 스크립트 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_release_gate_script_contains_required_checks() -> None:
    """release gate는 daemon/proxy, cli, critical lsp를 모두 검사해야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "run_release_gate.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "release-gate-summary.json" in content
    assert "daemon_proxy_passed" in content
    assert "cli_e2e_passed" in content
    assert "critical_lsp_passed" in content
    assert "mcp_handshake_passed" in content
    assert "mcp_concurrency_passed" in content
    assert "queue_ops_passed" in content
    assert "reconcile_passed" in content
    assert "reaped_lsp_by_language" in content
    assert "drain_failures" in content
    assert "reconcile strict-zero failed" in content
    assert "final_decision" in content
    assert "run_lsp_matrix_gate.sh --report-only true" in content
    assert "release-gate-critical-lsp.log" in content
    assert "release-gate-mcp-handshake.log" in content
    assert "release-gate-mcp-concurrency.log" in content
    assert "release-gate-queue-ops.log" in content
    assert "release-gate-reconcile.log" in content
    assert "\"logs\"" in content
    assert "\"probe_details\"" in content
    assert "\"probe_details_valid\"" in content
    assert "\"probe_validation_errors\"" in content
    assert "validate_probe_summary" in content
    assert "missing_or_invalid_summary" in content
    assert "release-gate-critical-fixture" in content
    assert "prepare_critical_fixture" in content
    assert "Main.java" in content
    assert "Main.kt" in content
    assert "Program.cs" in content
    assert "sari.csproj" in content
