"""MCP tools/list schema catalog를 제공한다."""

from __future__ import annotations

from sari.mcp.tool_registry import build_public_tool_schemas


def build_tools_list_result_payload(schema_version: str) -> dict[str, object]:
    """tools/list result payload를 구성한다."""
    return {
        "schemaVersion": schema_version,
        "schema_version": schema_version,
        "tools": build_public_tool_schemas(),
    }
