"""멀티 CLI soak 스크립트 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_mcp_multi_cli_soak_script_contains_required_guards() -> None:
    """soak 스크립트는 실패율 0/고아·좀비 0 가드를 강제해야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "run_mcp_multi_cli_soak.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "release_gate_mcp_probe.py soak" in content
    assert "SOAK_CLIENTS" in content
    assert "SOAK_DURATION_SEC" in content
    assert "SOAK_MAX_FAILURE_RATE" in content
    assert "SOAK_MAX_TIMEOUT_FAILURES" in content
    assert "mcp-multi-cli-soak-summary.json" in content
    assert "SARI_DB_PATH" in content
    assert "\"orphan_count\"" in content
    assert "\"zombie_count\"" in content
    assert "\"pass\"" in content
