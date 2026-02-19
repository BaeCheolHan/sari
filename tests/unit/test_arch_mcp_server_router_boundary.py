"""MCP 서버-포워딩 경계 가드를 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_mcp_server_does_not_embed_forward_retry_logic() -> None:
    """McpServer는 forward retry 구현을 직접 내장하면 안 된다."""
    root = Path(__file__).resolve().parents[2]
    target = root / "src/sari/mcp/server.py"
    source = target.read_text(encoding="utf-8")
    assert "forward_with_retry(" not in source
    assert "extract_workspace_root(" not in source
    assert "build_forward_error_message(" not in source
