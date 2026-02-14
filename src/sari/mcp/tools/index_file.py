from collections.abc import Mapping
from typing import TypeAlias

from sari.core.services.index_service import IndexService

from sari.mcp.tools._util import (
    mcp_response,
    pack_error,
    ErrorCode,
    resolve_db_path,
    handle_db_path_error,
    pack_header,
    pack_line,
    pack_encode_id,
    invalid_args_response,
    internal_error_response,
)

ToolResult: TypeAlias = dict[str, object]

def _code_str(code: object) -> str:
    return str(getattr(code, "value", code))


def execute_index_file(args: object, indexer: object, roots: list[str]) -> ToolResult:
    """
    특정 파일의 강제 재인덱싱을 수행합니다.
    (Force Re-indexing)
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("index_file", "args must be an object")

    path = str(args.get("path", "")).strip()
    if not path:
        return mcp_response(
            "index_file",
            lambda: pack_error("index_file", ErrorCode.INVALID_ARGS, "File path is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "File path is required"}, "isError": True},
        )

    if "\x00" in path:
        return invalid_args_response("index_file", "path contains NUL byte")

    db = getattr(indexer, "db", None)
    svc = IndexService(indexer)

    db_path = resolve_db_path(path, roots, db=db)
    if not db_path:
        return handle_db_path_error("index_file", path, roots, db)

    try:
        fs_path = path
        if hasattr(indexer, "_decode_db_path"):
            decode_fn = getattr(indexer, "_decode_db_path", None)
            decoded = decode_fn(db_path) if callable(decode_fn) else None
            if decoded:
                _, fs_path = decoded
                fs_path = str(fs_path)
        result = svc.index_file(fs_path)
        if not result.get("ok"):
            code = result.get("code", ErrorCode.INTERNAL)
            code_text = _code_str(code)
            message = result.get("message", "Indexer not available")
            data = result.get("data")
            return mcp_response(
                "index_file",
                lambda: pack_error("index_file", code_text, message, fields=data),
                lambda: {"error": {"code": code_text, "message": message, "data": data}, "isError": True},
            )

        def build_pack() -> str:
            lines = [pack_header("index_file", {}, returned=1)]
            lines.append(pack_line("m", {"path": pack_encode_id(db_path), "requested": "true"}))
            return "\n".join(lines)

        return mcp_response(
            "index_file",
            build_pack,
            lambda: {"success": True, "path": db_path, "message": f"Successfully requested re-indexing for {db_path}"},
        )
    except Exception as e:
        return internal_error_response(
            "index_file",
            e,
            code=ErrorCode.INTERNAL,
            reason_code="INDEX_FILE_EXECUTION_FAILED",
            data={"path": path[:512]},
            fallback_message="index_file failed",
        )
