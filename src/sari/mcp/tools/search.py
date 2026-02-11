#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
Universal search integration tool.
"""
import time
from typing import Mapping, TypeAlias, Optional, Tuple, List

from sari.core.settings import settings
from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_truncated,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    parse_search_options,
)
from sari.mcp.tools.inference import resolve_search_intent

# Import specialized executors for routing
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.repo_candidates import execute_repo_candidates

SearchArgs: TypeAlias = dict[str, object]
SearchMeta: TypeAlias = dict[str, object]
ToolResult: TypeAlias = dict[str, object]
SearchRoots: TypeAlias = list[str]


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _clip_text(value: object, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _validate_search_args(args: Mapping[str, object]) -> Optional[str]:
    """v3 parameter validation logic"""
    search_type = str(args.get("search_type", "code")).lower()

    # Mode-specific parameters check
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


def execute_search(
    args: SearchArgs,
    db: object,
    logger: object,
    roots: SearchRoots,
    engine: object = None,
    indexer: object = None,
) -> ToolResult:
    """
    v3 Unified Search Dispatcher.
    Routes requests to appropriate engines based on search_type or auto-inference.
    """
    # 0. v3 Parameter Validation
    validation_err = _validate_search_args(args)
    if validation_err:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, validation_err),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": validation_err,
                },
                "isError": True,
            },
        )

    start_ts = time.time()
    query = str(args.get("query", "")).strip()
    requested_type = str(args.get("search_type", "code")).lower()

    resolved_type = requested_type
    inference_blocked_reason = None
    fallback_used = False

    # 1. Intent Inference (if auto)
    if requested_type == "auto":
        resolved_type, inference_blocked_reason = resolve_search_intent(query)

    # 2. Dispatch to specialized executors
    if resolved_type == "symbol":
        result = execute_search_symbols(args, db, logger, roots)
        # Waterfall: if 0 results in auto mode, fallback to code
        if requested_type == "auto" and _is_empty_result(result):
            fallback_used = True
            resolved_type = "code"
            result = _execute_core_search(args, db, logger, roots, engine, indexer)
    elif resolved_type == "api":
        result = execute_search_api_endpoints(args, db, roots)
        # Waterfall: if 0 results in auto mode, fallback to code
        if requested_type == "auto" and _is_empty_result(result):
            fallback_used = True
            resolved_type = "code"
            result = _execute_core_search(args, db, logger, roots, engine, indexer)
    elif resolved_type == "repo":
        result = execute_repo_candidates(
            {"query": query, "limit": args.get("limit", 3)}, db, logger, roots
        )
    else:
        # Default: code search
        result = _execute_core_search(args, db, logger, roots, engine, indexer)

    # 3. Inject v3 Metadata (Only if not Error)
    # This part will be enhanced in Task 3 (Normalization)
    return result


def _is_empty_result(result: ToolResult) -> bool:
    """Checks if the MCP result contains 0 items."""
    try:
        # Check PACK1 format
        content_list = result.get("content", [])
        if content_list and isinstance(content_list, list):
            content = str(content_list[0].get("text", ""))
            if "returned=0" in content:
                return True
        # Check JSON format
        if "results" in result and len(result["results"]) == 0:
            return True
        if "hits" in result and len(result["hits"]) == 0:
            return True
    except Exception:
        pass
    return False


def _execute_core_search(
    args: SearchArgs,
    db: object,
    logger: object,
    roots: SearchRoots,
    engine: object = None,
    indexer: object = None,
) -> ToolResult:
    """Existing core search logic (from legacy search.py)"""
    start_ts = time.time()
    try:
        opts = parse_search_options(args, roots)
    except Exception as e:
        msg = str(e)
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, msg),
            lambda: {
                "error": {"code": ErrorCode.INVALID_ARGS.value, "message": msg},
                "isError": True,
            },
        )

    if not opts.query:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "query is required",
                },
                "isError": True,
            },
        )

    try:
        hits, meta = db.search_v2(opts)
    except Exception as e:
        msg = str(e)
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.ERR_ENGINE_QUERY, msg),
            lambda: {
                "error": {
                    "code": ErrorCode.ERR_ENGINE_QUERY.value,
                    "message": msg,
                },
                "isError": True,
            },
        )

    meta_map: Mapping[str, object] = meta if isinstance(meta, Mapping) else {}
    latency_ms = int((time.time() - start_ts) * 1000)
    total = int(meta_map.get("total", len(hits)))
    max_results = max(
        1, min(_safe_int(args.get("max_results"), opts.limit), opts.limit)
    )
    snippet_max_chars = max(
        80,
        min(
            _safe_int(
                args.get("snippet_max_chars"),
                getattr(settings, "MCP_SEARCH_SNIPPET_MAX_CHARS", 700),
            ),
            2000,
        ),
    )
    pack_max_bytes = max(
        4096,
        _safe_int(
            args.get("max_pack_bytes"),
            getattr(settings, "MCP_SEARCH_PACK_MAX_BYTES", 120000),
        ),
    )
    bounded_hits = hits[:max_results]

    def get_attr(obj: object, attr: str, default: object = "") -> object:
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def build_json() -> ToolResult:
        json_results = []
        for item in bounded_hits:
            if hasattr(item, "to_result_dict"):
                row = item.to_result_dict()
            elif isinstance(item, dict):
                row = dict(item)
            else:
                row = {
                    "path": str(get_attr(item, "path", "")),
                    "repo": str(get_attr(item, "repo", "")),
                    "score": float(get_attr(item, "score", 0.0)),
                    "file_type": str(get_attr(item, "file_type", "")),
                    "snippet": str(get_attr(item, "snippet", "")),
                    "hit_reason": str(get_attr(item, "hit_reason", "")),
                }
            row["snippet"] = _clip_text(row.get("snippet", ""), snippet_max_chars)
            json_results.append(row)
        return {
            "query": opts.query,
            "limit": opts.limit,
            "offset": opts.offset,
            "results": json_results,
            "meta": {
                **meta_map,
                "latency_ms": latency_ms,
                "returned": len(json_results),
                "bounded_by_max_results": len(hits) > len(json_results),
                "snippet_max_chars": snippet_max_chars,
            },
        }

    def build_pack() -> str:
        header = pack_header(
            "search",
            {"q": pack_encode_text(opts.query)},
            returned=len(bounded_hits),
        )
        lines = [header]
        used_bytes = len(header.encode("utf-8", errors="ignore")) + 1
        meta_line = pack_line(
            "m",
            {
                "total": str(total),
                "latency_ms": str(latency_ms),
                "engine": str(meta_map.get("engine", "unknown")),
            },
        )
        lines.append(meta_line)
        used_bytes += len(meta_line.encode("utf-8", errors="ignore")) + 1
        returned_count = 0
        hard_truncated = False
        for item in bounded_hits:
            imp_tag = ""
            hit_reason = str(get_attr(item, "hit_reason", ""))
            if "importance=" in hit_reason:
                try:
                    imp_val = hit_reason.split("importance=")[1].split(")")[0]
                    if float(imp_val) > 10.0:
                        imp_tag = " [CORE]"
                    elif float(imp_val) > 2.0:
                        imp_tag = " [SIG]"
                except Exception:
                    pass
            snippet = _clip_text(get_attr(item, "snippet"), snippet_max_chars)
            row_line = pack_line(
                "r",
                {
                    "path": pack_encode_id(get_attr(item, "path")),
                    "repo": pack_encode_id(get_attr(item, "repo")),
                    "score": f"{float(get_attr(item, 'score', 0.0)):.2f}",
                    "file_type": pack_encode_id(get_attr(item, "file_type")),
                    "snippet": pack_encode_text(snippet),
                    "rank_info": pack_encode_text(hit_reason + imp_tag),
                },
            )
            row_bytes = len(row_line.encode("utf-8", errors="ignore")) + 1
            if used_bytes + row_bytes > pack_max_bytes:
                hard_truncated = True
                break
            lines.append(row_line)
            used_bytes += row_bytes
            returned_count += 1
        soft_truncated = len(hits) > returned_count
        if hard_truncated or soft_truncated:
            next_offset = opts.offset + max(returned_count, 1)
            lines.append(pack_truncated(next_offset, opts.limit, "maybe"))
            lines.append(
                pack_line(
                    "m",
                    {
                        "budget_bytes": str(pack_max_bytes),
                        "returned": str(returned_count),
                    },
                )
            )
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)
