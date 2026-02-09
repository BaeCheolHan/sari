import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, List

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
)

from sari.core.queue_pipeline import DbTask


from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    parse_timestamp,
)

def execute_archive_context(args: Dict[str, Any], db: Any, roots: List[str], indexer: Any = None) -> Dict[str, Any]:
    """Archive knowledge context using the modernized Facade."""
    topic = str(args.get("topic") or "").strip()
    content = str(args.get("content") or "").strip()
    
    if not topic or not content:
        return mcp_response(
            "archive_context",
            lambda: pack_error("archive_context", ErrorCode.INVALID_ARGS, "topic and content are required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "topic and content are required"}, "isError": True},
        )

    # Prepare data for DTO-based upsert
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
        # Use Facade: db.contexts handles all the internal details
        payload = db.contexts.upsert(data)
    except Exception as e:
        return mcp_response(
            "archive_context",
            lambda: pack_error("archive_context", ErrorCode.DB_ERROR, str(e)),
            lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": str(e)}, "isError": True},
        )

    def build_pack() -> str:
        lines = [pack_header("archive_context", {"topic": pack_encode_text(payload.topic)}, returned=1)]
        kv = {
            "topic": pack_encode_id(payload.topic),
            "tags": pack_encode_text(",".join(payload.tags)),
        }
        lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response("archive_context", build_pack, lambda: payload.model_dump())
