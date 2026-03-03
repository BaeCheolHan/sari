"""설정 SSOT 범위 가드를 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_no_direct_getenv_in_mcp_http_scope() -> None:
    """MCP/HTTP 핵심 모듈은 직접 getenv를 호출하면 안 된다."""
    root = Path(__file__).resolve().parents[3]
    targets = [
        root / "src/sari/mcp/server.py",
        root / "src/sari/mcp/tools/search_tool.py",
        root / "src/sari/mcp/tools/read_tool.py",
        root / "src/sari/mcp/tools/symbol_graph_tools.py",
        root / "src/sari/mcp/tools/knowledge_tools.py",
        root / "src/sari/mcp/tools/status_tool.py",
        root / "src/sari/mcp/tools/sari_guide_tool.py",
        root / "src/sari/http/app.py",
    ]
    violations: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if "os.getenv(" in text:
            violations.append(str(path.relative_to(root)))
    assert violations == []
