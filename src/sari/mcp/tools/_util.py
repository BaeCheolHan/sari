import json
import os
import urllib.parse
from enum import Enum
from typing import Any, Dict, Optional, List, Callable, Tuple
import json
import os
import urllib.parse
from enum import Enum
from typing import Any, Dict, Optional, List, Callable, Tuple
from pathlib import Path
from sari.core.workspace import WorkspaceManager
from .protocol import (
    ErrorCode,
    pack_encode_text,
    pack_encode_id,
    pack_header,
    pack_line,
    pack_error,
    pack_truncated
)

def _default_error_hints(tool: str, code: Any, msg: str) -> List[str]:
    """Generate default hints/fallbacks for common failures."""
    hints: List[str] = []
    code_val = code.value if isinstance(code, Enum) else str(code)
    msg_lower = str(msg or "").lower()

    if "database" in msg_lower or "db" in msg_lower or code_val == ErrorCode.DB_ERROR.value:
        hints.extend([
            "run doctor to diagnose DB/engine 상태",
            "db_path 설정 확인 및 rescan",
        ])

    if code_val in {ErrorCode.NOT_INDEXED.value, ErrorCode.ERR_ENGINE_QUERY.value, ErrorCode.ERR_ENGINE_UNAVAILABLE.value}:
        hints.append("scan_once 또는 rescan으로 인덱싱 갱신")

    if tool in {"grep_and_read"}:
        hints.append("fallback: search -> read_file")
    if tool in {"repo_candidates"}:
        hints.append("fallback: list_files 요약 보기")
    if tool in {"search_api_endpoints"}:
        hints.append("repo 또는 root_ids를 명시해 스코프 고정")
    if tool in {"read_symbol", "get_callers", "get_implementations", "call_graph", "call_graph_health"}:
        hints.append("심볼 인덱싱 여부 확인 후 재시도")

    return hints

def require_db_schema(db: Any, tool: str, table: str, columns: List[str]):
    """Return error response if required table/columns are missing."""
    checker = getattr(db, "has_table_columns", None)
    if not checker:
        return None
    try:
        res = checker(table, columns)
        if not isinstance(res, tuple) or len(res) != 2:
            return None
        ok, missing = res
    except Exception:
        return None
    if ok:
        return None
    msg = f"DB schema mismatch: {table} missing columns: {', '.join(missing)}"
    return mcp_response(
        tool,
        lambda: pack_error(tool, ErrorCode.DB_ERROR, msg),
        lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": msg}, "isError": True},
    )

def get_data_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safe helper to get attribute from dict, Pydantic model, or object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    # Pydantic v2 or standard object
    return getattr(obj, attr, default)

def _get_env_any(key: str, default: str = "") -> str:
    """Read environment variable with multiple prefixes."""
    prefixes = ["SARI_", "CODEX_", "GEMINI_", ""]
    for p in prefixes:
        val = os.environ.get(p + key)
        if val is not None:
            return val
    return default

def _get_format() -> str:
    """Determine the response format (pack or json)."""
    fmt = _get_env_any("FORMAT", "pack").lower()
    return "pack" if fmt == "pack" else "json"

def _compact_enabled() -> bool:
    """Check if compact JSON output is enabled."""
    val = _get_env_any("RESPONSE_COMPACT", "1")
    return val.strip().lower() in ("1", "true", "yes", "on")

# --- Format Selection ---

def mcp_response(
    tool_name: str,
    pack_func: Callable[[], str],
    json_func: Callable[[], Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Helper to dispatch between PACK1 and JSON based on configuration.

    pack_func: function that returns (str) - the full PACK1 text payload.
    json_func: function that returns (dict) - the dict for JSON serialization.
    """
    fmt = _get_format()

    try:
        if fmt == "pack":
            text_output = pack_func()
            return {
                "content": [{"type": "text", "text": text_output}]
            }
        else:
            # JSON mode (Legacy/Debug)
            data = json_func()
            compact = _compact_enabled()
            
            # Ensure we return a proper MCP content structure
            json_text = json.dumps(data, ensure_ascii=False, 
                                   separators=(",", ":") if compact else None,
                                   indent=None if compact else 2)

            res = {"content": [{"type": "text", "text": json_text}]}
            # Lift metadata to top-level for easier client processing if it's a dict
            if isinstance(data, dict):
                for k, v in data.items():
                    if k not in res: # Don't overwrite MCP reserved keys
                        res[k] = v
            return res
    except Exception as e:
        import traceback
        err_msg = f"Internal Error in {tool_name}: {str(e)}"
        stack = traceback.format_exc()
        logger.error(err_msg, exc_info=True)

        if fmt == "pack":
            return {
                "content": [{"type": "text", "text": pack_error(tool_name, ErrorCode.INTERNAL, err_msg, trace=stack)}],
                "isError": True
            }
        else:
            return {
                "content": [{"type": "text", "text": json.dumps({"error": err_msg, "trace": stack})}],
                "isError": True,
                "error": {"code": ErrorCode.INTERNAL.value, "message": err_msg}
            }


def mcp_json(obj):
    """Utility to format dictionary as standard MCP response."""
    if _compact_enabled():
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(obj, ensure_ascii=False, indent=2)
    res = {"content": [{"type": "text", "text": payload}]}
    if isinstance(obj, dict):
        res.update(obj)
    return res


def resolve_root_ids(roots: List[str]) -> List[str]:
    if not roots or not WorkspaceManager:
        return []
    out: List[str] = []
    allow_legacy = str(os.environ.get("SARI_ALLOW_LEGACY", "")).strip().lower() in {"1", "true", "yes", "on"}
    for r in roots:
        try:
            out.append(WorkspaceManager.root_id_for_workspace(r))
            if allow_legacy:
                out.append(WorkspaceManager.root_id(r))
        except Exception:
            continue
    return list(dict.fromkeys(out))

def parse_timestamp(v: Any) -> int:
    """Robustly parse ISO8601 strings or integers into unix timestamps."""
    if v is None or v == "": return 0
    if isinstance(v, (int, float)): return int(v)
    s = str(v).strip()
    if s.isdigit(): return int(s)
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception: return 0

def parse_search_options(args: Dict[str, Any], roots: List[str]) -> Any:
    """Standardized SearchOptions parser for MCP tools."""
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


def _intersect_preserve_order(base: List[str], rhs: List[str]) -> List[str]:
    rhs_set = set(rhs)
    return [x for x in base if x in rhs_set]


def resolve_repo_scope(
    repo: Optional[str],
    roots: List[str],
    db: Optional[Any] = None,
) -> Tuple[Optional[str], List[str]]:
    """
    Resolve repo argument into:
    - effective_repo: value for files.repo exact filter (when repo token is truly repo bucket like 'src')
    - effective_root_ids: root scope inferred from root labels/names/paths and DB metadata
    """
    allowed_root_ids = resolve_root_ids(roots)
    repo_raw = str(repo or "").strip()
    if not repo_raw:
        return None, allowed_root_ids

    q = repo_raw.lower()
    allow_legacy = str(os.environ.get("SARI_ALLOW_LEGACY", "")).strip().lower() in {"1", "true", "yes", "on"}
    matched_root_ids: List[str] = []
    for r in roots or []:
        try:
            rp = Path(r).expanduser().resolve()
            name = rp.name.lower()
            full = str(rp).lower()
            if q == name or q == full or (q and q in name):
                matched_root_ids.append(WorkspaceManager.root_id_for_workspace(str(rp)))
                if allow_legacy:
                    matched_root_ids.append(WorkspaceManager.root_id(str(rp)))
        except Exception:
            continue

    db_repo_root_ids: List[str] = []
    db_root_match_ids: List[str] = []
    if db is not None:
        conn = getattr(db, "_read", None)
        if conn is None and hasattr(db, "get_read_connection"):
            try:
                conn = db.get_read_connection()
            except Exception:
                conn = None
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT root_id FROM files WHERE LOWER(COALESCE(repo, '')) = LOWER(?)",
                    (repo_raw,),
                ).fetchall()
                db_repo_root_ids = [str(r[0]) for r in rows if r and r[0]]
            except Exception:
                db_repo_root_ids = []
            try:
                rows = conn.execute(
                    "SELECT root_id FROM roots WHERE LOWER(COALESCE(label, '')) = LOWER(?) OR LOWER(COALESCE(root_path, '')) LIKE ?",
                    (repo_raw, f"%/{q}%"),
                ).fetchall()
                db_root_match_ids = [str(r[0]) for r in rows if r and r[0]]
            except Exception:
                db_root_match_ids = []

    if db_root_match_ids:
        matched_root_ids.extend(db_root_match_ids)

    if matched_root_ids:
        if allowed_root_ids:
            return None, _intersect_preserve_order(allowed_root_ids, matched_root_ids)
        return None, list(dict.fromkeys(matched_root_ids))

    if db_repo_root_ids:
        if allowed_root_ids:
            return repo_raw, _intersect_preserve_order(allowed_root_ids, db_repo_root_ids)
        return repo_raw, list(dict.fromkeys(db_repo_root_ids))

    return repo_raw, allowed_root_ids

def _is_safe_relative_path(rel: str) -> bool:
    if rel is None:
        return False
    rel = str(rel).strip()
    if not rel:
        return False
    p = Path(rel)
    if p.is_absolute():
        return False
    # Block traversal and Windows drive-like segments.
    for part in p.parts:
        if part in {"..", ""}:
            return False
        if ":" in part:
            return False
    return True


def resolve_db_path(input_path: str, roots: List[str]) -> Optional[str]:
    """
    Converts a filesystem path to a Sari DB path (root_id/relative_path).
    Supports absolute path root_ids and handles nested workspaces using Longest Prefix Match.
    """
    if not input_path or not roots or not WorkspaceManager:
        return None

    try:
        # Normalize target path
        p = Path(os.path.expanduser(input_path)).resolve()
    except Exception:
        return None

    # Resolve all roots once to avoid redundant I/O and ensure accurate sorting
    resolved_roots = []
    for r in roots:
        try:
            resolved_roots.append(Path(r).expanduser().resolve())
        except Exception:
            continue

    # Sort roots by number of path components (depth) descending to ensure specific match first
    sorted_roots = sorted(resolved_roots, key=lambda x: len(x.parts), reverse=True)
    
    for root_path in sorted_roots:
        try:
            # Check if target is inside this root
            if p == root_path or root_path in p.parents:
                rel = p.relative_to(root_path).as_posix()
                if not _is_safe_relative_path(rel) and p != root_path:
                    continue
                # The root_id is the normalized absolute path of the workspace
                rid = WorkspaceManager.root_id_for_workspace(str(root_path))
                return f"{rid}/{rel}" if rel != "." else rid
        except Exception:
            continue
    return None


def resolve_fs_path(db_path: str, roots: List[str]) -> Optional[str]:
    """
    Resolves a Sari DB path back to a real filesystem path.
    Instead of simple splitting, it matches against known active roots.
    """
    if not db_path or not roots or not WorkspaceManager:
        return None

    # Normalize all active root IDs
    active_root_map = {}
    for r in roots:
        try:
            rid = WorkspaceManager.root_id_for_workspace(r)
            active_root_map[rid] = Path(r).expanduser().resolve()
        except Exception:
            continue

    # Sort root IDs by length descending to match the most specific one first
    sorted_rids = sorted(active_root_map.keys(), key=len, reverse=True)

    for rid in sorted_rids:
        if db_path.startswith(rid):
            # rid might be the whole db_path or a prefix followed by a slash
            if len(db_path) == len(rid):
                return str(active_root_map[rid])
            elif db_path[len(rid)] == "/":
                rel = db_path[len(rid) + 1:]
                if not _is_safe_relative_path(rel):
                    continue
                candidate = (active_root_map[rid] / rel).resolve()
                # Final safety check: ensure the resulting path is still inside the root
                if candidate == active_root_map[rid] or active_root_map[rid] in candidate.parents:
                    return str(candidate)
    
    return None
