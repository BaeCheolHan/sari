"""
Central utility aggregator for Sari MCP tools.
This module re-exports common functionality from modular components to maintain backward compatibility.
"""
import logging
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TypeAlias

# --- Protocol & Formatting ---
from .protocol import (
    ErrorCode,
    mcp_response,
    pack_error,
    pack_header,
    pack_line,
    pack_encode_text,
    pack_encode_id,
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
from .diagnostics import handle_db_path_error, require_db_schema

# --- Small generic helpers (remain here for now) ---
logger = logging.getLogger("sari.mcp.tools")

ToolResult: TypeAlias = dict[str, object]
ArgMap: TypeAlias = Mapping[str, object]


def _is_string_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if _is_string_sequence(value):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    text = str(value).strip()
    return [text] if text else []


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def get_data_attr(obj: object, attr: str, default: object = None) -> object:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def parse_timestamp(v: object) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return 0


def invalid_args_response(tool: str, message: str) -> ToolResult:
    return mcp_response(
        tool,
        lambda: pack_error(tool, ErrorCode.INVALID_ARGS, message),
        lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": message}, "isError": True},
    )


def _error_code_text(code: object) -> str:
    if isinstance(code, Enum):
        return str(code.value)
    return str(code)


def sanitize_error_message(exc: object, fallback: str = "Internal error") -> str:
    raw = str(exc).strip() if exc is not None else ""
    if not raw:
        return fallback
    compact = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    return compact[:500] if len(compact) > 500 else compact


def internal_error_response(
    tool: str,
    exc: object,
    *,
    code: object = ErrorCode.INTERNAL,
    reason_code: str | None = None,
    data: Mapping[str, object] | None = None,
    fallback_message: str = "Internal error",
) -> ToolResult:
    code_text = _error_code_text(code)
    msg = sanitize_error_message(exc, fallback_message)
    reason = str(reason_code or f"{tool.upper()}_FAILED")
    merged_data: dict[str, object] = {"reason_code": reason}
    if data:
        merged_data.update(dict(data))

    pack_fields: dict[str, object] = {"reason_code": reason}
    if data:
        for key, value in data.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                pack_fields[str(key)] = value

    return mcp_response(
        tool,
        lambda: pack_error(tool, code_text, msg, fields=pack_fields),
        lambda: {
            "error": {"code": code_text, "message": msg, "data": merged_data},
            "isError": True,
        },
    )


def parse_int_arg(
    args: ArgMap,
    key: str,
    default: int,
    tool: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> tuple[int | None, ToolResult | None]:
    raw = args.get(key, default)
    try:
        value = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        try:
            f_value = float(raw if raw is not None else default)
        except (TypeError, ValueError):
            return None, invalid_args_response(tool, f"'{key}' must be an integer")
        if not f_value.is_integer():
            return None, invalid_args_response(tool, f"'{key}' must be an integer")
        value = int(f_value)
    if min_value is not None and value < min_value:
        return None, invalid_args_response(tool, f"'{key}' must be >= {min_value}")
    if max_value is not None and value > max_value:
        return None, invalid_args_response(tool, f"'{key}' must be <= {max_value}")
    return value, None


def _intersect_preserve_order(base: list[str], rhs: list[str]) -> list[str]:
    rhs_set = set(rhs)
    return [x for x in base if x in rhs_set]


def parse_search_options(args: ArgMap, roots: list[str], db: object = None) -> object:
    from sari.core.models import SearchOptions

    root_ids = resolve_root_ids(roots)
    req_root_ids = args.get("root_ids")
    if _is_string_sequence(req_root_ids):
        req_ids = [str(r) for r in req_root_ids if r]
        root_ids = _intersect_preserve_order(root_ids, req_ids) if root_ids else list(req_ids)

    repo_raw = args.get("scope") or args.get("repo")
    repo_value = str(repo_raw).strip() if repo_raw is not None else None
    resolved_repo, repo_root_ids = resolve_repo_scope(repo_value, roots, db=db)
    if repo_root_ids:
        root_ids = _intersect_preserve_order(root_ids, repo_root_ids) if root_ids else list(repo_root_ids)
    path_pattern_raw = args.get("path_pattern")
    path_pattern = str(path_pattern_raw) if path_pattern_raw is not None else None

    return SearchOptions(
        query=str(args.get("query") or "").strip(),
        repo=resolved_repo or None,
        limit=max(1, min(int(args.get("limit", 8) or 8), 100)),
        offset=max(int(args.get("offset", 0) or 0), 0),
        snippet_lines=min(max(int(args.get("context_lines", 5) or 5), 1), 20),
        file_types=_normalize_string_list(args.get("file_types")),
        path_pattern=path_pattern,
        exclude_patterns=_normalize_string_list(args.get("exclude_patterns")),
        recency_boost=_coerce_bool(args.get("recency_boost", False)),
        use_regex=_coerce_bool(args.get("use_regex", False)),
        case_sensitive=_coerce_bool(args.get("case_sensitive", False)),
        total_mode=str(args.get("total_mode") or "exact").strip().lower(),
        root_ids=root_ids,
    )


def require_repo_arg(args: ArgMap, tool: str) -> ToolResult | None:
    repo_raw = args.get("scope") or args.get("repo")
    if isinstance(repo_raw, str) and repo_raw.strip():
        return None
    return invalid_args_response(tool, "repo is required")


__all__ = [
    "ErrorCode",
    "mcp_response",
    "pack_error",
    "pack_header",
    "pack_line",
    "pack_encode_text",
    "pack_encode_id",
    "pack_truncated",
    "invalid_args_response",
    "parse_int_arg",
    "resolve_root_ids",
    "resolve_db_path",
    "resolve_fs_path",
    "resolve_repo_scope",
    "handle_db_path_error",
    "require_db_schema",
    "get_data_attr",
    "parse_timestamp",
    "parse_search_options",
    "require_repo_arg",
    "sanitize_error_message",
    "internal_error_response",
]
