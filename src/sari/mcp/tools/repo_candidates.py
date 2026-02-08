#!/usr/bin/env python3
"""
Repo candidates tool for Sari MCP Server.
"""
import json
from typing import Any, Dict, List
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_id, pack_encode_text, pack_error, ErrorCode, resolve_root_ids, require_db_schema

from sari.core.db import LocalSearchDB
from sari.mcp.telemetry import TelemetryLogger


def execute_repo_candidates(args: Dict[str, Any], db: LocalSearchDB, logger: TelemetryLogger = None, roots: List[str] = None) -> Dict[str, Any]:
    """Execute repo_candidates tool."""
    guard = require_db_schema(
        db,
        "repo_candidates",
        "files",
        ["path", "rel_path", "root_id", "repo", "deleted_ts", "fts_content"],
    )
    if guard:
        return guard
    query = args.get("query", "")
    try:
        limit_arg = min(int(args.get("limit", 3)), 5)
    except (ValueError, TypeError):
        limit_arg = 3

    if not query.strip():
        return mcp_response(
            "repo_candidates",
            lambda: pack_error("repo_candidates", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "query is required"}, "isError": True},
        )

    def get_candidates():
        root_ids = resolve_root_ids(list(roots or []))
        candidates = db.repo_candidates(q=query, limit=limit_arg, root_ids=root_ids)
        for candidate in candidates:
            score = candidate.get("score", 0)
            if score >= 10:
                reason = f"High match ({score} files contain '{query}')"
            elif score >= 5:
                reason = f"Moderate match ({score} files)"
            else:
                reason = f"Low match ({score} files)"
            candidate["reason"] = reason
        return candidates

    # --- JSON Builder ---
    def build_json() -> Dict[str, Any]:
        candidates = get_candidates()
        return {
            "query": query,
            "candidates": candidates,
            "hint": "Use 'repo' parameter in search to narrow down scope after selection",
        }

    # --- PACK1 Builder ---
    def build_pack() -> str:
        candidates = get_candidates()

        # Header
        kv = {"q": pack_encode_text(query), "limit": limit_arg}
        lines = [
            pack_header("repo_candidates", kv, returned=len(candidates))
        ]

        # Records
        for c in candidates:
            # r:repo=<repo> score=<score> reason=<reason>
            kv_line = {
                "repo": pack_encode_id(c["repo"]),
                "score": str(c["score"]),
                "reason": pack_encode_text(c["reason"])
            }
            lines.append(pack_line("r", kv_line))

        return "\n".join(lines)

    if logger and hasattr(logger, "log_telemetry"):
        logger.log_telemetry(f"tool=repo_candidates query='{query}' limit={limit_arg}")

    return mcp_response("repo_candidates", build_pack, build_json)
