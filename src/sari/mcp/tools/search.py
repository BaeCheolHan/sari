#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
"""
import os
import time
from typing import Any, Dict, List

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_truncated,
    pack_encode_id,
    pack_encode_text,
    resolve_root_ids,
    pack_error,
    ErrorCode,
)

from sari.core.db import LocalSearchDB, SearchOptions
from sari.core.engine_runtime import EngineError, SqliteSearchEngineAdapter
from sari.core.services.search_service import SearchService
from sari.mcp.telemetry import TelemetryLogger


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

def execute_search(
    args: Dict[str, Any],
    db: Any,
    logger: Any,
    roots: List[str],
    engine: Any = None,
    indexer: Any = None,
) -> Dict[str, Any]:
    """Execute hybrid search using the modernized Facade."""
    start_ts = time.time()
    
    # 1. Standardized Options Parsing
    try:
        opts = parse_search_options(args, roots)
    except Exception as e:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, str(e)),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": str(e)}, "isError": True},
        )

    if not opts.query:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "query is required"}, "isError": True},
        )

    # 2. Hybrid Search via DB Facade (Handles Tantivy vs SQLite automatically)
    try:
        hits, meta = db.search_v2(opts)
    except Exception as e:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.ERR_ENGINE_QUERY, str(e)),
            lambda: {"error": {"code": ErrorCode.ERR_ENGINE_QUERY.value, "message": str(e)}, "isError": True},
        )

    latency_ms = int((time.time() - start_ts) * 1000)
    total = meta.get("total", len(hits))
    
    def build_json() -> Dict[str, Any]:
        return {
            "query": opts.query, "limit": opts.limit, "offset": opts.offset,
            "results": [h.to_result_dict() if hasattr(h, "to_result_dict") else h for h in hits],
            "meta": {**meta, "latency_ms": latency_ms}
        }

    def build_pack() -> str:
        returned = len(hits)
        header = pack_header("search", {"q": pack_encode_text(opts.query)}, returned=returned)
        lines = [header]
        lines.append(pack_line("m", {"total": str(total), "latency_ms": str(latency_ms), "engine": meta.get("engine", "unknown")}))
        
        for h in hits:
            # Extract importance from hit_reason if possible for visual feedback
            imp_tag = ""
            if "importance=" in h.hit_reason:
                try:
                    imp_val = h.hit_reason.split("importance=")[1].split(")")[0]
                    if float(imp_val) > 10.0: imp_tag = " [CORE]"
                    elif float(imp_val) > 2.0: imp_tag = " [SIG]"
                except: pass

            lines.append(pack_line("r", {
                "path": pack_encode_id(h.path),
                "repo": pack_encode_id(h.repo),
                "score": f"{h.score:.2f}",
                "file_type": pack_encode_id(h.file_type),
                "snippet": pack_encode_text(h.snippet),
                "rank_info": pack_encode_text(h.hit_reason + imp_tag),
            }))
        if returned >= opts.limit:
            lines.append(pack_truncated(opts.offset + opts.limit, opts.limit, "maybe"))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)

    latency_ms = int((time.time() - start_ts) * 1000)
    total = meta.get("total", -1)
    total_mode = meta.get("total_mode", total_mode)
    partial = bool(meta.get("partial", False))
    db_health = meta.get("db_health", "ok")
    db_error = meta.get("db_error", "")

    index_meta = service.index_meta()
    index_ready = index_meta.get("index_ready") if index_meta else None
    indexed_files = index_meta.get("indexed_files") if index_meta else None
    scanned_files = index_meta.get("scanned_files") if index_meta else None
    index_errors = index_meta.get("index_errors") if index_meta else None
    if index_ready is False:
        partial = True

    def build_json() -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        for hit in hits:
            if hasattr(hit, "to_result_dict"):
                results.append(hit.to_result_dict())
            else:
                results.append({
                    "doc_id": hit.path,
                    "repo": hit.repo,
                    "path": hit.path,
                    "score": hit.score,
                    "snippet": hit.snippet,
                    "mtime": hit.mtime,
                    "size": hit.size,
                    "match_count": hit.match_count,
                    "file_type": hit.file_type,
                    "hit_reason": hit.hit_reason,
                    "scope_reason": hit.scope_reason,
                    "context_symbol": getattr(hit, "context_symbol", ""),
                    "docstring": getattr(hit, "docstring", ""),
                    "metadata": getattr(hit, "metadata", {}),
                })
        return {
            "query": query,
            "limit": limit,
            "offset": offset,
            "results": results,
            "meta": {
                "total": total,
                "total_mode": total_mode,
                "engine": engine_mode,
                "latency_ms": latency_ms,
                "index_version": index_version,
                "partial": partial,
                "db_health": db_health,
                "db_error": db_error,
                "index_ready": index_ready,
                "indexed_files": indexed_files,
                "scanned_files": scanned_files,
                "index_errors": index_errors,
            },
        }

    def build_pack() -> str:
        returned = len(hits)
        header = pack_header("search", {"q": pack_encode_text(query)}, returned=returned)
        lines = [header]
        lines.append(pack_line("m", {"total": str(total)}))
        lines.append(pack_line("m", {"total_mode": total_mode}))
        lines.append(pack_line("m", {"engine": engine_mode}))
        lines.append(pack_line("m", {"latency_ms": str(latency_ms)}))
        if index_version:
            lines.append(pack_line("m", {"index_version": pack_encode_id(index_version)}))
        lines.append(pack_line("m", {"partial": str(partial).lower()}))
        if db_health:
            lines.append(pack_line("m", {"db_health": pack_encode_id(db_health)}))
        if db_error:
            lines.append(pack_line("m", {"db_error": pack_encode_text(db_error)}))
        if index_ready is not None:
            lines.append(pack_line("m", {"index_ready": str(index_ready).lower()}))
        if indexed_files is not None:
            lines.append(pack_line("m", {"indexed_files": str(indexed_files)}))
        if scanned_files is not None:
            lines.append(pack_line("m", {"scanned_files": str(scanned_files)}))
        if index_errors is not None:
            lines.append(pack_line("m", {"index_errors": str(index_errors)}))
        
        # Efficiency hint for LLMs (Serena-inspired)
        lines.append(pack_line("m", {"efficiency_hint": "To save tokens, use list_symbols <path> to see file structure before read_file."}))

        for h in hits:
            lines.append(pack_line("r", {
                "path": pack_encode_id(h.path),
                "repo": pack_encode_id(h.repo),
                "score": f"{h.score:.3f}",
                "mtime": str(h.mtime),
                "size": str(h.size),
                "file_type": pack_encode_id(h.file_type),
                "snippet": pack_encode_text(h.snippet),
                "hit_reason": pack_encode_text(h.hit_reason),
                "scope_reason": pack_encode_text(h.scope_reason),
            }))
        if returned >= limit:
            lines.append(pack_truncated(offset + limit, limit, "maybe"))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)
