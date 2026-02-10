"""
Central utility aggregator for Sari MCP tools.
This module re-exports common functionality from modular components to maintain backward compatibility.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sari.mcp.tools")

# --- Protocol & Formatting ---
from .protocol import (
    ErrorCode,
    mcp_response,
    pack_encode_text,
    pack_encode_id,
    pack_header,
    pack_line,
    pack_error,
    pack_truncated,
)

# --- Path Resolution & Scoping ---
from .resolution import (
    resolve_root_ids,
    resolve_db_path,
    resolve_fs_path,
    resolve_repo_scope,
)

# --- Diagnostics & Guidance ---
from .diagnostics import (
    handle_db_path_error,
    require_db_schema,
)

# --- Small generic helpers (remain here for now) ---

def get_data_attr(obj: Any, attr: str, default: Any = None) -> Any:
    if obj is None: return default
    if isinstance(obj, dict): return obj.get(attr, default)
    return getattr(obj, attr, default)

def parse_timestamp(v: Any) -> int:
    if v is None or v == "": return 0
    if isinstance(v, (int, float)): return int(v)
    s = str(v).strip()
    if s.isdigit(): return int(s)
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception: return 0

def _intersect_preserve_order(base: List[str], rhs: List[str]) -> List[str]:
    rhs_set = set(rhs)
    return [x for x in base if x in rhs_set]

def parse_search_options(args: Dict[str, Any], roots: List[str]) -> Any:
    from sari.core.models import SearchOptions
    root_ids = resolve_root_ids(roots)
    req_root_ids = args.get("root_ids")
    if isinstance(req_root_ids, list):
        req_root_ids = [str(r) for r in req_root_ids if r]
        root_ids = [r for r in root_ids if r in req_root_ids] if root_ids else list(req_root_ids)

    return SearchOptions(
        query=(args.get("query") or "").strip(),
        repo=args.get("scope") or args.get("repo"),
        limit=max(1, min(int(args.get("limit", 8) or 8), 50)),
        offset=max(int(args.get("offset", 0) or 0), 0),
        snippet_lines=min(max(int(args.get("context_lines", 5) or 5), 1), 20),
        file_types=list(args.get("file_types", [])),
        path_pattern=args.get("path_pattern"),
        exclude_patterns=args.get("exclude_patterns", []),
        recency_boost=bool(args.get("recency_boost", False)),
        use_regex=bool(args.get("use_regex", False)),
        case_sensitive=bool(args.get("case_sensitive", False)),
        total_mode=str(args.get("total_mode") or "exact").strip().lower(),
        root_ids=root_ids,
    )