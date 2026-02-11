from collections.abc import Mapping
from typing import TypeAlias

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    parse_timestamp,
    invalid_args_response,
)

ToolResult: TypeAlias = dict[str, object]

def execute_archive_context(
    args: object,
    db: object,
    roots: list[str],
    indexer: object = None,
) -> ToolResult:
    """
    도메인 지식이나 작업 컨텍스트를 보관(Archive)하는 도구입니다.
    Facade 패턴을 사용하여 지식 컨텍스트를 DB에 안전하게 저장합니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("archive_context", "args must be an object")

    topic = str(args.get("topic") or "").strip()
    content = str(args.get("content") or "").strip()
    
    if not topic or not content:
        return mcp_response(
            "archive_context",
            lambda: pack_error("archive_context", ErrorCode.INVALID_ARGS, "topic and content are required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "topic and content are required"}, "isError": True},
        )

    # DTO 기반 업서트를 위한 데이터 준비
    data = {
        "topic": topic,
        "content": content,
        "tags": args.get("tags") or [],
        "related_files": args.get("related_files") or [],
        "source": str(args.get("source") or "").strip(),
        "valid_from": parse_timestamp(args.get("valid_from")),
        "valid_until": parse_timestamp(args.get("valid_until")),
        "deprecated": bool(args.get("deprecated")),
    }

    try:
        # Facade 사용: db.contexts가 모든 내부 세부 사항을 처리합니다.
        payload = db.contexts.upsert(data)
    except Exception as e:
        msg = str(e)
        return mcp_response(
            "archive_context",
            lambda: pack_error("archive_context", ErrorCode.DB_ERROR, msg),
            lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": msg}, "isError": True},
        )

    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        lines = [pack_header("archive_context", {"topic": pack_encode_text(payload.topic)}, returned=1)]
        kv = {
            "topic": pack_encode_id(payload.topic),
            "tags": pack_encode_text(",".join(payload.tags)),
        }
        lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response("archive_context", build_pack, lambda: payload.model_dump())
