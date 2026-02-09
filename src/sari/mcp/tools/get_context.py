from typing import Any, Dict, List

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_error,
    ErrorCode,
    require_db_schema,
    parse_timestamp,
)

def execute_get_context(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    """
    저장된 도메인 지식이나 작업 컨텍스트를 조회하는 도구입니다.
    특정 주제(Topic)로 직접 조회하거나 검색 쿼리를 통한 전문 검색을 지원합니다.
    """
    guard = require_db_schema(db, "get_context", "contexts", ["topic", "content"])
    if guard: return guard

    topic = str(args.get("topic") or "").strip()
    query = str(args.get("query") or "").strip()
    as_of = parse_timestamp(args.get("as_of"))
    limit = int(args.get("limit") or 20)

    try:
        if topic:
            # 주제별 직접 조회
            row = db.contexts.get_context_by_topic(topic, as_of=as_of)
            results = [row] if row else []
        elif query:
            # 검색 쿼리를 통한 조회
            results = db.contexts.search_contexts(query, limit=limit, as_of=as_of)
        else:
            return mcp_response(
                "get_context",
                lambda: pack_error("get_context", ErrorCode.INVALID_ARGS, "topic or query is required"),
                lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "topic or query is required"}, "isError": True},
            )
    except Exception as e:
        return mcp_response(
            "get_context",
            lambda: pack_error("get_context", ErrorCode.DB_ERROR, str(e)),
            lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": str(e)}, "isError": True},
        )

    def build_json() -> Dict[str, Any]:
        """JSON 형식의 응답을 생성합니다."""
        return {
            "topic": topic, "query": query,
            "results": [r.model_dump() for r in results],
            "count": len(results)
        }

    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        lines = [pack_header("get_context", {}, returned=len(results))]
        for r in results:
            kv = {
                "topic": pack_encode_id(r.topic),
                "updated_ts": str(r.updated_ts),
                "deprecated": str(int(r.deprecated)),
            }
            lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response("get_context", build_pack, build_json)
