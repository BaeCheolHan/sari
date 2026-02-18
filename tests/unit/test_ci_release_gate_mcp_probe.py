"""release gate MCP probe 스크립트 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_release_gate_mcp_probe_uses_structured_summary_and_safe_pkill() -> None:
    """probe는 구조화된 요약과 비-shell pkill 경로를 제공해야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "release_gate_mcp_probe.py"
    content = script_path.read_text(encoding="utf-8")

    assert "PROBE_SUMMARY:" in content
    assert "subprocess.run([\"pkill\", \"-f\", \"sari.*daemon\"]" in content
    assert "subprocess.run([\"pkill\", \"-f\", \"sari daemon run\"]" in content
    assert "shell=True" not in content


def test_release_gate_script_embeds_probe_details_into_summary() -> None:
    """release gate summary JSON에 probe 상세가 포함되어야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "run_release_gate.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "\"probe_details\"" in content
    assert "extract_probe_summary" in content
    assert "PROBE_SUMMARY:" in content
