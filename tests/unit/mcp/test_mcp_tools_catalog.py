"""tools/list 스키마 payload catalog helper를 검증한다."""

from sari.mcp.tool_registry import build_tool_handler_attr_map
from sari.mcp.tools_catalog import build_tools_list_result_payload


def test_build_tools_list_result_payload_includes_schema_versions_and_core_tools() -> None:
    """catalog helper는 schema version과 핵심 도구 스키마를 유지해야 한다."""
    payload = build_tools_list_result_payload("2026-02-18.pack1.v2-line")

    assert payload["schemaVersion"] == "2026-02-18.pack1.v2-line"
    assert payload["schema_version"] == "2026-02-18.pack1.v2-line"
    tools = payload["tools"]
    assert isinstance(tools, list)
    tool_names = {tool["name"] for tool in tools}
    assert "search" in tool_names
    assert "read" in tool_names
    assert "status" in tool_names


def test_tool_handler_attr_map_covers_tools_list_names() -> None:
    """tools/list에 노출되는 도구 이름은 handler registry에도 존재해야 한다."""
    payload = build_tools_list_result_payload("v")
    tool_names = {tool["name"] for tool in payload["tools"]}
    handler_map = build_tool_handler_attr_map()

    assert tool_names.issubset(set(handler_map.keys()))
    assert handler_map["search"] == "_search_tool"
