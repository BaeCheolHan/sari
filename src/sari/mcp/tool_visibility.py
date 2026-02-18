"""MCP 도구 노출 정책을 정의한다."""

from __future__ import annotations

from copy import deepcopy


HIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "save_snippet",
        "get_snippet",
        "archive_context",
        "get_context",
        "pipeline_policy_get",
        "pipeline_policy_set",
        "pipeline_alert_status",
        "pipeline_dead_list",
        "pipeline_dead_requeue",
        "pipeline_dead_purge",
        "pipeline_auto_status",
        "pipeline_auto_set",
        "pipeline_auto_tick",
        "pipeline_benchmark_run",
        "pipeline_benchmark_report",
        "pipeline_quality_run",
        "pipeline_quality_report",
        "pipeline_lsp_matrix_run",
        "pipeline_lsp_matrix_report",
    }
)

TOOL_EXAMPLES: dict[str, dict[str, object]] = {
    "search": {"repo": "/repo", "query": "AuthService", "limit": 5},
    "read": {"repo": "/repo", "mode": "file", "target": "README.md", "limit": 40},
    "read_symbol": {"repo": "/repo", "symbol": "AuthService.login", "limit": 10},
    "search_symbol": {"repo": "/repo", "query": "Auth", "limit": 10},
    "get_callers": {"repo": "/repo", "symbol": "AuthService.login", "limit": 20},
    "get_implementations": {"repo": "/repo", "symbol": "UserRepository", "limit": 20},
    "call_graph": {"repo": "/repo", "symbol": "AuthService.login", "limit": 20},
    "knowledge": {"repo": "/repo", "query": "JWT", "limit": 5},
    "list_symbols": {"repo": "/repo", "query": "Auth", "limit": 20},
}


def is_hidden_tool_name(tool_name: object) -> bool:
    """지정 도구명이 숨김 정책 대상인지 반환한다."""
    return isinstance(tool_name, str) and tool_name in HIDDEN_TOOL_NAMES


def filter_tools_list_response_payload(payload: dict[str, object]) -> dict[str, object]:
    """tools/list JSON-RPC 응답에서 숨김 도구를 제거한다."""
    result_obj = payload.get("result")
    if not isinstance(result_obj, dict):
        return payload
    tools_obj = result_obj.get("tools")
    if not isinstance(tools_obj, list):
        return payload
    filtered_tools = [_decorate_tool_entry(tool) for tool in tools_obj if not _is_hidden_tool_entry(tool)]
    if len(filtered_tools) == len(tools_obj):
        return payload
    copied_payload = deepcopy(payload)
    copied_result = copied_payload.get("result")
    if isinstance(copied_result, dict):
        copied_result["tools"] = filtered_tools
    return copied_payload


def _is_hidden_tool_entry(entry: object) -> bool:
    """tools/list 항목이 숨김 대상인지 판정한다."""
    if not isinstance(entry, dict):
        return False
    return is_hidden_tool_name(entry.get("name"))


def _decorate_tool_entry(entry: object) -> dict[str, object]:
    """LLM 친화 도구 힌트를 tools/list 항목에 주입한다."""
    if not isinstance(entry, dict):
        return {}
    tool_name_raw = entry.get("name")
    tool_name = tool_name_raw if isinstance(tool_name_raw, str) else ""
    payload = deepcopy(entry)
    example = TOOL_EXAMPLES.get(tool_name)
    if example is not None:
        payload["x_examples"] = [example]
    return payload
