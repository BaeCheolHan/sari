#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
Universal search integration tool.
"""
import time
import json
import re
from typing import Mapping, TypeAlias, Optional, Tuple, List, Dict, Any

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    parse_search_options,
    resolve_root_ids,
)
from sari.mcp.tools.inference import resolve_search_intent

# Import specialized executors for routing
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.repo_candidates import execute_repo_candidates

SearchArgs: TypeAlias = dict[str, object]
ToolResult: TypeAlias = dict[str, object]
SearchRoots: TypeAlias = list[str]


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
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


class PreviewManager:
    """Manages token budget by dynamically adjusting preview lengths."""

    def __init__(self, limit: int, max_total_chars: int = 10000):
        self.limit = limit
        self.max_total_chars = max_total_chars
        self.degraded = False

    def get_adjusted_max_chars(self, item_count: int, requested_max: int) -> int:
        if item_count <= 0:
            return requested_max

        # Budget-based calculation
        budget_per_item = self.max_total_chars // item_count
        if budget_per_item < requested_max:
            self.degraded = True
            return max(100, budget_per_item)
        return requested_max


def _validate_search_args(args: Mapping[str, object]) -> Optional[str]:
    """v3 parameter validation logic"""
    search_type = str(args.get("search_type", "code")).lower()
    allowed_types = {"code", "symbol", "api", "repo", "auto"}

    if search_type not in allowed_types:
        return f"Invalid search_type: '{search_type}'. Must be one of {sorted(list(allowed_types))}"

    # Basic numeric validation to avoid falling into INTERNAL-style errors later.
    int_params = ("limit", "offset", "context_lines", "max_preview_chars")
    for name in int_params:
        if name in args:
            try:
                int(args.get(name))
            except (TypeError, ValueError):
                return f"'{name}' must be an integer."

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
    """v3 Unified Search Dispatcher with response normalization."""
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
    limit = _safe_int(args.get("limit"), 20)

    resolved_type = requested_type
    inference_blocked_reason = None
    fallback_used = False

    # 1. Intent Inference (if auto)
    if requested_type == "auto":
        resolved_type, inference_blocked_reason = resolve_search_intent(query)

    # 2. Dispatch and Get Raw Results
    raw_result = None

    if requested_type == "auto" and resolved_type in ("symbol", "api"):
        if resolved_type == "symbol":
            raw_result = execute_search_symbols(args, db, logger, roots)
        else:
            api_args = dict(args)
            if "query" in api_args and "path" not in api_args:
                api_args["path"] = api_args["query"]
            raw_result = execute_search_api_endpoints(api_args, db, roots)

        if raw_result.get("isError") or _is_empty_result(raw_result):
            fallback_used = True
            resolved_type = "code"
            raw_result = _execute_core_search_raw(args, db, logger, roots, engine, indexer)

    # CASE: Explicit types (Sequential is fine)
    elif resolved_type == "symbol":
        raw_result = execute_search_symbols(args, db, logger, roots)
    elif resolved_type == "api":
        api_args = dict(args)
        if "query" in api_args and "path" not in api_args:
            api_args["path"] = api_args["query"]
        raw_result = execute_search_api_endpoints(api_args, db, roots)
    elif resolved_type == "repo":
        raw_result = execute_repo_candidates({"query": query, "limit": limit}, db, logger, roots)
    else:
        # Default: code search
        raw_result = _execute_core_search_raw(args, db, logger, roots, engine, indexer)

    if raw_result.get("isError"):
        return raw_result

    # 3. Response Normalization & Token Management
    latency_ms = int((time.time() - start_ts) * 1000)
    normalized_matches, total = _normalize_results(resolved_type, raw_result)

    # Token Budget Logic
    preview_mode = str(args.get("preview_mode", "snippet")).lower()
    pm = PreviewManager(limit)
    requested_max = _safe_int(args.get("max_preview_chars"), 1200)
    adjusted_max = pm.get_adjusted_max_chars(len(normalized_matches), requested_max)

    for match in normalized_matches:
        if preview_mode == "none":
            match.pop("snippet", None)
        elif "snippet" in match:
            match["snippet"] = _clip_text(match["snippet"], adjusted_max)

    # 4. Final Output Building
    repo_suggestions = []
    if total == 0 and args.get("fallback_repo_suggestions", True) and hasattr(db, "repo_candidates"):
        try:
            repo_suggestions = db.repo_candidates(
                query,
                limit=3,
                root_ids=resolve_root_ids(roots),
            )
        except Exception:
            pass

    v3_meta = {
        "total": total,
        "latency_ms": latency_ms,
        "preview_degraded": pm.degraded,
        "requested_type": requested_type,
        "resolved_type": resolved_type,
        "fallback_used": fallback_used,
        "inference_blocked_reason": inference_blocked_reason,
    }

    def build_json() -> ToolResult:
        return {
            "ok": True,
            "mode": resolved_type,
            "query": query,
            "meta": v3_meta,
            "matches": normalized_matches,
            "repo_suggestions": repo_suggestions if resolved_type != "repo" else []
        }

    def build_pack() -> str:
        header = pack_header(
            "search",
            {"q": pack_encode_text(query)},
            returned=len(normalized_matches),
            total=total,
        )
        lines = [header]
        lines.append(
            pack_line("m", {k: str(v) for k, v in v3_meta.items() if v is not None})
        )
        for m in normalized_matches:
            lines.append(
                pack_line(
                    "r",
                    {
                        "t": m["type"],
                        "p": pack_encode_id(m["path"]),
                        "i": pack_encode_text(m["identity"]),
                        "l": str(m["location"].get("line", 0)),
                        "s": pack_encode_text(m.get("snippet", "")) if preview_mode != "none" else ""
                    },
                )
            )
        if repo_suggestions and resolved_type != "repo":
            for rs in repo_suggestions:
                lines.append(pack_line("repo_hint", {"name": rs.get("repo", ""), "score": str(rs.get("score", 0))}))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)


def _extract_first_line_number(snippet: str) -> int:
    """Extracts the first line number from a snippet (pattern: L123: content)."""
    if not snippet:
        return 0
    match = re.search(r"L(\d+):", snippet)
    if match:
        return int(match.group(1))
    return 0


def _normalize_results(
    res_type: str, raw: ToolResult
) -> Tuple[List[Dict[str, Any]], int]:
    matches = []
    total = 0

    try:
        # Unwrap mcp_response if needed
        if "content" in raw and isinstance(raw["content"], list):
            content_item = raw["content"][0]
            if isinstance(content_item, dict):
                text = content_item.get("text", "{}")
                if text.strip().startswith("{"):
                    raw = json.loads(text)

        if res_type == "symbol":
            results = raw.get("results", [])
            total = raw.get("count", len(results))
            for r in results:
                matches.append(
                    {
                        "type": "symbol",
                        "path": r.get("path"),
                        "identity": r.get("name"),
                        "location": {
                            "line": r.get("line"),
                            "qualname": r.get("qualname"),
                        },
                        "extra": {"kind": r.get("kind")},
                    }
                )
        elif res_type == "api":
            results = raw.get("results", [])
            total = len(results)
            for r in results:
                matches.append(
                    {
                        "type": "api",
                        "path": r.get("file", ""),
                        "identity": r.get("path", ""),
                        "location": {"line": r.get("line", 0)},
                        "extra": {"method": r.get("method"), "handler": r.get("handler")},
                    }
                )
        elif res_type == "repo":
            results = raw.get("candidates", [])
            total = len(results)
            for r in results:
                matches.append(
                    {
                        "type": "repo",
                        "path": r.get("repo", ""),
                        "identity": r.get("repo", ""),
                        "location": {},
                        "extra": {"score": r.get("score")},
                    }
                )
        else:  # code
            results = raw.get("results", [])
            meta = raw.get("meta", {})
            total = meta.get("total", len(results)) if isinstance(meta, dict) else len(results)
            for r in results:
                snippet = r.get("snippet", "")
                first_line = _extract_first_line_number(snippet)
                matches.append(
                    {
                        "type": "code",
                        "path": r.get("path"),
                        "identity": str(r.get("path", "")).split("/")[-1],
                        "location": {"line": first_line},
                        "snippet": snippet,
                        "extra": {"repo": r.get("repo"), "score": r.get("score")},
                    }
                )
    except Exception:
        pass

    return matches, total


def _is_empty_result(result: ToolResult) -> bool:
    try:
        if "content" in result and isinstance(result["content"], list):
            content_item = result["content"][0]
            if isinstance(content_item, dict):
                text = content_item.get("text", "")
                if text.strip().startswith("{"):
                    data = json.loads(text)
                    if "results" in data and len(data["results"]) == 0:
                        return True
                    if "hits" in data and len(data["hits"]) == 0:
                        return True
                    if "candidates" in data and len(data["candidates"]) == 0:
                        return True
                elif "returned=0" in text:
                    return True

        if "results" in result and len(result["results"]) == 0:
            return True
        if "hits" in result and len(result["hits"]) == 0:
            return True
        if "candidates" in result and len(result["candidates"]) == 0:
            return True
    except Exception:
        pass
    return False


def _execute_core_search_raw(
    args: SearchArgs,
    db: object,
    logger: object,
    roots: SearchRoots,
    engine: object,
    indexer: object,
) -> ToolResult:
    """Core search logic that returns raw results for normalization."""
    try:
        opts = parse_search_options(args, roots)
        hits, meta = db.search_v2(opts)
        results = []
        for h in hits:
            results.append(
                {
                    "path": h.path,
                    "repo": h.repo,
                    "score": h.score,
                    "snippet": h.snippet,
                    "mtime": h.mtime,
                    "size": h.size,
                    "file_type": h.file_type,
                    "hit_reason": h.hit_reason,
                }
            )
        return {"results": results, "meta": meta}
    except Exception as e:
        return {"isError": True, "error": {"message": str(e)}}
