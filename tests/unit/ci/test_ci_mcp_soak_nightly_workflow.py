"""MCP soak nightly 워크플로우 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_mcp_soak_nightly_uses_multi_cli_script_and_summary_artifact() -> None:
    """nightly soak는 전용 스크립트와 summary 아티팩트를 사용해야 한다."""
    root = Path(__file__).resolve().parents[3]
    workflow_path = root / ".github" / "workflows" / "mcp-soak-nightly.yml"
    content = workflow_path.read_text(encoding="utf-8")

    assert "tools/ci/run_mcp_multi_cli_soak.sh" in content
    assert "SOAK_CLIENTS" in content
    assert "SOAK_MAX_FAILURE_RATE: \"0.0\"" in content
    assert "artifacts/ci/mcp-multi-cli-soak-summary.json" in content

