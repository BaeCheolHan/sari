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
        "pipeline_perf_run",
        "pipeline_perf_report",
        "pipeline_quality_run",
        "pipeline_quality_report",
        "pipeline_lsp_matrix_run",
        "pipeline_lsp_matrix_report",
    }
)

TOOL_EXAMPLES: dict[str, dict[str, object]] = {
    "search": {"repo_id": "sari", "query": "AuthService", "limit": 5},
    "read": {"repo_id": "sari", "mode": "file", "target": "README.md", "limit": 40},
    "read_symbol": {"repo_id": "sari", "symbol": "AuthService.login", "limit": 10},
    "search_symbol": {"repo_id": "sari", "query": "Auth", "limit": 10},
    "get_callers": {"repo_id": "sari", "symbol": "AuthService.login", "limit": 20},
    "get_implementations": {"repo_id": "sari", "symbol": "UserRepository", "limit": 20},
    "call_graph": {"repo_id": "sari", "symbol": "AuthService.login", "limit": 20},
    "knowledge": {"repo_id": "sari", "query": "JWT", "limit": 5},
    "list_symbols": {"repo_id": "sari", "query": "Auth", "limit": 20},
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
    if filtered_tools == tools_obj:
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
    payload = _decorate_repo_id_hint(payload)
    return payload


def _decorate_repo_id_hint(entry: dict[str, object]) -> dict[str, object]:
    """repo 입력 스키마를 repo_id 우선 안내로 보강한다."""
    schema_obj = entry.get("inputSchema")
    if not isinstance(schema_obj, dict):
        return entry
    properties_obj = schema_obj.get("properties")
    if not isinstance(properties_obj, dict):
        return entry
    repo_obj = properties_obj.get("repo")
    if not isinstance(repo_obj, dict):
        return entry
    repo_description = "repository id(권장). alias: repo(하위호환)."
    properties_obj["repo_id"] = {
        "type": "string",
        "description": repo_description,
    }
    if "description" not in repo_obj:
        repo_obj["description"] = repo_description
    return entry
