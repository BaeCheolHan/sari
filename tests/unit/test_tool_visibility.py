"""tools/list 노출 정책 후처리를 검증한다."""

from sari.mcp.tool_visibility import filter_tools_list_response_payload


def test_filter_tools_list_injects_repo_id_hint_and_examples() -> None:
    """search 도구에는 repo_id 힌트와 예시가 주입되어야 한다."""
    payload = {
        "result": {
            "tools": [
                {
                    "name": "search",
                    "description": "Search",
                    "inputSchema": {
                        "type": "object",
                        "required": ["repo", "query"],
                        "properties": {
                            "repo": {"type": "string"},
                            "query": {"type": "string"},
                        },
                    },
                }
            ]
        }
    }
    filtered = filter_tools_list_response_payload(payload)
    tools_obj = filtered.get("result", {}).get("tools", [])
    assert isinstance(tools_obj, list)
    assert len(tools_obj) == 1
    tool = tools_obj[0]
    assert tool.get("x_examples") == [{"repo_id": "sari", "query": "AuthService", "limit": 5}]
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    assert "repo_id" in props
