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
    filtered_tools = [tool for tool in tools_obj if not _is_hidden_tool_entry(tool)]
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
