from __future__ import annotations

from typing import Mapping

from sari.mcp.tools._util import parse_search_options
from sari.mcp.tools.inference import resolve_search_intent
from sari.mcp.tools.repo_candidates import execute_repo_candidates
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.search_normalize import is_empty_result
from sari.mcp.tools.search_symbols import execute_search_symbols

SearchArgs = dict[str, object]
ToolResult = dict[str, object]
SearchRoots = list[str]


def validate_search_args(args: Mapping[str, object]) -> str | None:
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


def execute_core_search_raw(
    args: SearchArgs,
    db: object,
    roots: SearchRoots,
) -> ToolResult:
    try:
        opts = parse_search_options(args, roots)
        search_fn = getattr(db, "search", None)
        if not callable(search_fn):
            raise RuntimeError("No search backend available (search)")
        hits, meta = search_fn(opts)
        if hits is None:
            normalized_hits: list[object] = []
        elif isinstance(hits, Mapping):
            normalized_hits = [hits]
        elif isinstance(hits, (list, tuple)):
            normalized_hits = list(hits)
        else:
            try:
                normalized_hits = list(hits)
            except TypeError:
                normalized_hits = []
        results = []
        for h in normalized_hits:
            if isinstance(h, Mapping):
                path = h.get("path", "")
                repo = h.get("repo", "")
                score = h.get("score", 0.0)
                snippet = h.get("snippet", "")
                mtime = h.get("mtime", 0)
                size = h.get("size", 0)
                file_type = h.get("file_type", "")
                hit_reason = h.get("hit_reason", "")
            else:
                path = getattr(h, "path", "")
                repo = getattr(h, "repo", "")
                score = getattr(h, "score", 0.0)
                snippet = getattr(h, "snippet", "")
                mtime = getattr(h, "mtime", 0)
                size = getattr(h, "size", 0)
                file_type = getattr(h, "file_type", "")
                hit_reason = getattr(h, "hit_reason", "")
            results.append(
                {
                    "path": path,
                    "repo": repo,
                    "score": score,
                    "snippet": snippet,
                    "mtime": mtime,
                    "size": size,
                    "file_type": file_type,
                    "hit_reason": hit_reason,
                }
            )
        return {"results": results, "meta": meta}
    except Exception as e:
        return {"isError": True, "error": {"message": str(e)}}


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
            raw_result = symbol_executor(args, db, logger, roots)
        else:
            api_args = dict(args)
            if "query" in api_args and "path" not in api_args:
                api_args["path"] = api_args["query"]
            raw_result = api_executor(api_args, db, roots)
        if raw_result.get("isError") or is_empty_result(raw_result):
            fallback_used = True
            resolved_type = "code"
            raw_result = execute_core_search_raw(args, db, roots)
    elif resolved_type == "symbol":
        raw_result = symbol_executor(args, db, logger, roots)
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
        raw_result = execute_core_search_raw(args, db, roots)

    return raw_result, resolved_type, inference_blocked_reason, fallback_used, limit
