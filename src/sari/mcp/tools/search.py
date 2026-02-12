#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TypeAlias

from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.session_state import record_search_metrics
from sari.mcp.tools._util import (
    ErrorCode,
    mcp_response,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    pack_header,
    pack_line,
    resolve_root_ids,
)
from sari.mcp.tools.search_dispatch import dispatch_search, validate_search_args
from sari.mcp.tools.search_normalize import normalize_results
from sari.mcp.tools.search_symbols import execute_search_symbols
from sari.mcp.tools.search_api_endpoints import execute_search_api_endpoints
from sari.mcp.tools.repo_candidates import execute_repo_candidates
from sari.mcp.tools.search_stabilization import (
    PreviewManager,
    bundle_id,
    candidate_id,
    clip_text,
    next_calls_for_matches,
    safe_int,
)

SearchArgs: TypeAlias = dict[str, object]
ToolResult: TypeAlias = dict[str, object]
SearchRoots: TypeAlias = list[str]

# Backward-compat exports for tests/legacy imports.
_clip_text = clip_text
_safe_int = safe_int


def _search_error_response(code: str, message: str, *, query: str = "target") -> ToolResult:
    next_calls = [
        {
            "tool": "search",
            "arguments": {"query": str(query or "target"), "search_type": "code", "limit": 5},
        }
    ]
    return mcp_response(
        "search",
        lambda: pack_error("search", code, message),
        lambda: {
            "error": {"code": code, "message": message},
            "meta": {
                "stabilization": {
                    "reason_codes": [str(code or "UNKNOWN")],
                    "suggested_next_action": "search",
                    "warnings": [str(message or "search failed")],
                    "next_calls": next_calls,
                }
            },
            "isError": True,
        },
    )


def execute_search(
    args: object,
    db: object,
    logger: object,
    roots: SearchRoots,
    engine: object = None,
    indexer: object = None,
) -> ToolResult:
    del engine, indexer
    if not isinstance(args, Mapping):
        return _search_error_response(ErrorCode.INVALID_ARGS.value, "args must be an object")
    args = dict(args)
    validation_err = validate_search_args(args)
    if validation_err:
        return _search_error_response(ErrorCode.INVALID_ARGS.value, validation_err)

    start_ts = time.time()
    query = str(args.get("query", "")).strip()
    requested_type = str(args.get("search_type", "code")).lower()

    raw_result, resolved_type, inference_blocked_reason, fallback_used, limit = dispatch_search(
        args,
        db=db,
        logger=logger,
        roots=roots,
        symbol_executor=execute_search_symbols,
        api_executor=execute_search_api_endpoints,
        repo_executor=execute_repo_candidates,
    )

    if raw_result.get("isError"):
        err = raw_result.get("error", {})
        code = ErrorCode.INTERNAL.value
        message = "Search failed"
        if isinstance(err, dict):
            raw_code = str(err.get("code", "")).strip()
            raw_message = str(err.get("message", "")).strip()
            if raw_code:
                code = raw_code
            if raw_message:
                message = raw_message
        return _search_error_response(str(code), message, query=query)

    latency_ms = int((time.time() - start_ts) * 1000)
    normalized_matches, total, normalization_warnings = normalize_results(resolved_type, raw_result)

    preview_mode = str(args.get("preview_mode", "snippet")).lower()
    pm = PreviewManager(limit)
    requested_max = safe_int(args.get("max_preview_chars"), 1200)
    adjusted_max = pm.get_adjusted_max_chars(len(normalized_matches), requested_max)
    for match in normalized_matches:
        if preview_mode == "none":
            match.pop("snippet", None)
        elif "snippet" in match:
            match["snippet"] = clip_text(match["snippet"], adjusted_max)

    repo_suggestions = []
    if total == 0 and args.get("fallback_repo_suggestions", True) and hasattr(db, "repo_candidates"):
        try:
            repo_suggestions = db.repo_candidates(query, limit=3, root_ids=resolve_root_ids(roots))
        except Exception:
            pass

    candidate_map: dict[str, str] = {}
    for idx, match in enumerate(normalized_matches):
        cid = candidate_id(match, idx)
        match["candidate_id"] = cid
        candidate_map[cid] = str(match.get("path", ""))
    bundle = bundle_id(query, normalized_matches)
    next_calls = next_calls_for_matches(normalized_matches, bundle)
    if not next_calls:
        next_calls = [
            {
                "tool": "search",
                "arguments": {
                    "query": query or "target",
                    "search_type": "code",
                    "limit": max(5, limit),
                },
            }
        ]

    metrics_snapshot = record_search_metrics(
        args,
        roots,
        preview_degraded=pm.degraded,
        query=query,
        top_paths=[str(m.get("path", "")) for m in normalized_matches[:10]],
        candidates=candidate_map,
        bundle_id=bundle,
        db=db,
    )

    v3_meta = {
        "total": total,
        "latency_ms": latency_ms,
        "preview_degraded": pm.degraded,
        "requested_type": requested_type,
        "resolved_type": resolved_type,
        "fallback_used": fallback_used,
        "inference_blocked_reason": inference_blocked_reason,
    }
    if normalization_warnings:
        v3_meta["normalization_warnings"] = normalization_warnings

    reason_codes: list[str] = []
    if pm.degraded:
        reason_codes.append(ReasonCode.PREVIEW_DEGRADED.value)

    json_meta = dict(v3_meta)
    json_meta["stabilization"] = {
        "budget_state": "NORMAL",
        "suggested_next_action": "read" if total > 0 else "search",
        "warnings": [],
        "reason_codes": reason_codes,
        "bundle_id": bundle,
        "next_calls": next_calls,
        "metrics_snapshot": metrics_snapshot,
    }

    def build_json() -> ToolResult:
        return {
            "ok": True,
            "mode": resolved_type,
            "query": query,
            "meta": json_meta,
            "matches": normalized_matches,
            "repo_suggestions": repo_suggestions if resolved_type != "repo" else [],
        }

    def build_pack() -> str:
        header = pack_header("search", {"q": pack_encode_text(query)}, returned=len(normalized_matches), total=total)
        lines = [header]
        lines.append(pack_line("m", {k: str(v) for k, v in v3_meta.items() if v is not None}))
        for m in normalized_matches:
            lines.append(
                pack_line(
                    "r",
                    {
                        "t": m["type"],
                        "p": pack_encode_id(m["path"]),
                        "i": pack_encode_text(m["identity"]),
                        "l": str(m["location"].get("line", 0)),
                        "s": pack_encode_text(m.get("snippet", "")) if preview_mode != "none" else "",
                    },
                )
            )
        if repo_suggestions and resolved_type != "repo":
            for rs in repo_suggestions:
                lines.append(pack_line("repo_hint", {"name": rs.get("repo", ""), "score": str(rs.get("score", 0))}))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)
