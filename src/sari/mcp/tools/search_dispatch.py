from __future__ import annotations

from typing import Mapping

from sari.mcp.tools.candidate_search import execute_candidate_search_raw
from sari.mcp.tools.inference import resolve_search_intent
from sari.mcp.tools.repo_candidates import execute_repo_candidates
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.search_normalize import is_empty_result
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.symbol_resolve import execute_symbol_resolve

SearchArgs = dict[str, object]
ToolResult = dict[str, object]
SearchRoots = list[str]


def validate_search_args(args: Mapping[str, object]) -> str | None:
    if bool(args.get("__enforce_repo__", False)):
        repo = args.get("scope") or args.get("repo")
        if not isinstance(repo, str) or not repo.strip():
            return "repo is required."

    search_type = str(args.get("search_type", "code")).lower()
    allowed_types = {"code", "symbol", "api", "repo", "auto"}
    if search_type not in allowed_types:
        return f"Invalid search_type: '{search_type}'. Must be one of {sorted(list(allowed_types))}"

    int_params = ("limit", "offset", "context_lines", "max_preview_chars")
    for name in int_params:
        if name in args:
            try:
                int(args.get(name))
            except (TypeError, ValueError):
                return f"'{name}' must be an integer."

    symbol_only = {"kinds", "match_mode", "include_qualname"}
    api_only = {"method", "framework_hint"}
    if search_type != "symbol" and search_type != "auto":
        for p in symbol_only:
            if p in args:
                return f"'{p}' is only valid for search_type='symbol'."
    if search_type != "api" and search_type != "auto":
        for p in api_only:
            if p in args:
                return f"'{p}' is only valid for search_type='api'."

    return None


def execute_core_search_raw(args: SearchArgs, db: object, roots: SearchRoots) -> ToolResult:
    # Backward-compatible alias for existing callers/tests.
    return execute_candidate_search_raw(args, db, roots)


def dispatch_search(
    args: SearchArgs,
    *,
    db: object,
    logger: object,
    roots: SearchRoots,
    symbol_executor=execute_search_symbols,
    api_executor=execute_search_api_endpoints,
    repo_executor=execute_repo_candidates,
) -> tuple[ToolResult, str, str | None, bool, int]:
    query = str(args.get("query", "")).strip()
    requested_type = str(args.get("search_type", "code")).lower()
    limit = int(args.get("limit", 20) or 20)

    resolved_type = requested_type
    inference_blocked_reason = None
    fallback_used = False

    if requested_type == "auto":
        resolved_type, inference_blocked_reason = resolve_search_intent(query)

    raw_result: ToolResult
    if requested_type == "auto" and resolved_type in ("symbol", "api"):
        if resolved_type == "symbol":
            raw_result = execute_symbol_resolve(
                args, db=db, logger=logger, roots=roots, symbol_executor=symbol_executor
            )
        else:
            api_args = dict(args)
            if "query" in api_args and "path" not in api_args:
                api_args["path"] = api_args["query"]
            raw_result = api_executor(api_args, db, roots)
        if raw_result.get("isError") or is_empty_result(raw_result):
            fallback_used = True
            resolved_type = "code"
            raw_result = execute_candidate_search_raw(args, db, roots)
    elif resolved_type == "symbol":
        raw_result = execute_symbol_resolve(
            args, db=db, logger=logger, roots=roots, symbol_executor=symbol_executor
        )
    elif resolved_type == "api":
        api_args = dict(args)
        if "query" in api_args and "path" not in api_args:
            api_args["path"] = api_args["query"]
        raw_result = api_executor(api_args, db, roots)
    elif resolved_type == "repo":
        repo_args = {"query": query, "limit": limit}
        if "root_ids" in args:
            repo_args["root_ids"] = args["root_ids"]
        raw_result = repo_executor(repo_args, db, logger, roots)
    else:
        raw_result = execute_candidate_search_raw(args, db, roots)

    return raw_result, resolved_type, inference_blocked_reason, fallback_used, limit
