from typing import Any, Dict, List

try:
    from ._util import (
        mcp_response,
        pack_header,
        pack_line,
        pack_encode_id,
        pack_encode_text,
        pack_error,
        ErrorCode,
    )
except ImportError:
    from _util import (
        mcp_response,
        pack_header,
        pack_line,
        pack_encode_id,
        pack_encode_text,
        pack_error,
        ErrorCode,
    )


def build_get_context(args: Dict[str, Any], db: Any) -> Dict[str, Any]:
    topic = str(args.get("topic") or "").strip()
    query = str(args.get("query") or "").strip()
    limit = int(args.get("limit") or 20)
    as_of_raw = args.get("as_of")
    def _parse_ts(v):
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
    as_of = _parse_ts(as_of_raw)

    def _is_active(row):
        if not row:
            return False
        if row.get("deprecated"):
            return False
        if as_of:
            vf = int(row.get("valid_from") or 0)
            vu = int(row.get("valid_until") or 0)
            if vf and as_of < vf:
                return False
            if vu and as_of > vu:
                return False
        return True
    if topic:
        row = db.get_context_by_topic(topic)
        if as_of and row and not _is_active(row):
            row = None
        return {"topic": topic, "results": [row] if row else []}
    if query:
        rows = db.search_contexts(query, limit=limit)
        if as_of:
            rows = [r for r in rows if _is_active(r)]
        return {"query": query, "results": rows}
    raise ValueError("topic or query is required")


def execute_get_context(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    try:
        payload = build_get_context(args, db)
    except ValueError as e:
        return mcp_response(
            "get_context",
            lambda: pack_error("get_context", ErrorCode.INVALID_ARGS, str(e)),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": str(e)}, "isError": True},
        )

    def build_pack() -> str:
        lines = [pack_header("get_context", {}, returned=len(payload.get("results", [])))]
        for r in payload.get("results", []):
            kv = {
                "topic": pack_encode_id(r.get("topic", "")),
                "updated_ts": str(r.get("updated_ts", 0)),
                "deprecated": str(int(r.get("deprecated") or 0)),
            }
            lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response(
        "get_context",
        build_pack,
        lambda: payload,
    )
