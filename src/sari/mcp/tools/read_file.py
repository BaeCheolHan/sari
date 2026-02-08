from typing import Any, Dict, List
from sari.core.db import LocalSearchDB
from sari.mcp.tools._util import mcp_response, pack_error, ErrorCode, resolve_db_path, pack_header, pack_line, pack_encode_text

def execute_read_file(args: Dict[str, Any], db: LocalSearchDB, roots: List[str]) -> Dict[str, Any]:
    """
    Execute read_file tool with support for line-based pagination.

    Args:
        args: {"path": str, "offset": int, "limit": int}
        db: LocalSearchDB instance
    """
    path = args.get("path")
    offset = int(args.get("offset", 0))
    limit = args.get("limit")
    if limit is not None:
        limit = int(limit)

    if not path:
        return mcp_response(
            "read_file",
            lambda: pack_error("read_file", ErrorCode.INVALID_ARGS, "'path' is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "'path' is required"}, "isError": True},
        )

    db_path = resolve_db_path(path, roots)
    if not db_path and db.has_legacy_paths():
        db_path = path
    if not db_path:
        return mcp_response(
            "read_file",
            lambda: pack_error("read_file", ErrorCode.ERR_ROOT_OUT_OF_SCOPE, f"Path out of scope: {path}", hints=["outside final_roots"]),
            lambda: {"error": {"code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value, "message": f"Path out of scope: {path}"}, "isError": True},
        )

    content = db.read_file(db_path)
    if content is None:
        return mcp_response(
            "read_file",
            lambda: pack_error(
                "read_file",
                ErrorCode.NOT_INDEXED,
                f"File not found or not indexed: {db_path}",
                hints=["run scan_once", "verify path with search"],
            ),
            lambda: {
                "error": {
                    "code": ErrorCode.NOT_INDEXED.value,
                    "message": f"File not found or not indexed: {db_path}",
                    "hint": "run scan_once | verify path with search",
                },
                "isError": True,
            },
        )

    # Line-based pagination logic
    lines = content.splitlines()
    total_lines = len(lines)
    
    if limit is not None:
        end = offset + limit
        paged_lines = lines[offset:end]
        is_truncated = end < total_lines
        content = "\n".join(paged_lines)
    else:
        paged_lines = lines[offset:]
        is_truncated = False
        content = "\n".join(paged_lines)

    # Token counting logic (Serena-inspired efficiency metrics)
    token_count = 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(content))
    except Exception:
        token_count = len(content) // 4 # Fallback approx

    def build_pack() -> str:
        # Include pagination and token metadata in header
        kv = {"offset": offset, "total_lines": total_lines, "tokens": token_count}
        if limit is not None:
            kv["limit"] = limit
        if is_truncated:
            kv["truncated"] = "true"
            kv["next_offset"] = offset + len(paged_lines)
        if token_count > 2000:
            kv["warning"] = "High token usage. Consider using list_symbols or read_symbol."

        lines_out = [pack_header("read_file", kv, returned=1)]
        # Use encoded text for consistency with other tools and test expectations
        lines_out.append(f"t:{pack_encode_text(content)}")
        return "\n".join(lines_out)

    return mcp_response(
        "read_file",
        build_pack,
        lambda: {
            "content": [{"type": "text", "text": content}],
            "metadata": {
                "offset": offset,
                "limit": limit,
                "total_lines": total_lines,
                "is_truncated": is_truncated,
                "token_count": token_count,
                "efficiency_warning": "High token usage" if token_count > 2000 else None
            }
        },
    )
