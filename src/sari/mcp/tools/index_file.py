from typing import Any, Dict, List

from sari.core.services.index_service import IndexService

from sari.mcp.tools._util import mcp_response, pack_error, ErrorCode, resolve_db_path, handle_db_path_error, pack_header, pack_line, pack_encode_id

def execute_index_file(args: Dict[str, Any], indexer: Any, roots: List[str]) -> Dict[str, Any]:
    """
    특정 파일의 강제 재인덱싱을 수행합니다.
    (Force Re-indexing)
    """
    path = args.get("path", "").strip()
    if not path:
        return mcp_response(
            "index_file",
            lambda: pack_error("index_file", ErrorCode.INVALID_ARGS, "File path is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "File path is required"}, "isError": True},
        )

    db = getattr(indexer, "db", None)
    svc = IndexService(indexer)

    db_path = resolve_db_path(path, roots, db=db)
    if not db_path:
        return handle_db_path_error("index_file", path, roots, db)

    try:
        fs_path = path
        if hasattr(indexer, "_decode_db_path"):
            decoded = indexer._decode_db_path(db_path)  # type: ignore[attr-defined]
            if decoded:
                _, fs_path = decoded
                fs_path = str(fs_path)
        result = svc.index_file(fs_path)
        if not result.get("ok"):
            code = result.get("code", ErrorCode.INTERNAL)
            message = result.get("message", "Indexer not available")
            data = result.get("data")
            return mcp_response(
                "index_file",
                lambda: pack_error("index_file", code, message, fields=data),
                lambda: {"error": {"code": code.value, "message": message, "data": data}, "isError": True},
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
        return mcp_response(
            "index_file",
            lambda: pack_error("index_file", ErrorCode.INTERNAL, str(e)),
            lambda: {"error": {"code": ErrorCode.INTERNAL.value, "message": str(e)}, "isError": True},
        )
