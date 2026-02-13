from collections.abc import Mapping
from typing import TypeAlias

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    resolve_root_ids,
    resolve_repo_scope,
    pack_error,
    ErrorCode,
    parse_int_arg,
    invalid_args_response,
)
from sari.mcp.tools.call_graph import build_call_graph
from sari.core.models import CallerHitDTO

ToolResult: TypeAlias = dict[str, object]


def _is_next_candidate_path(path: str) -> bool:
    p = str(path or "").strip().lower()
    if not p:
        return False
    blocked_tokens = ("/.idea/", "/.vscode/", "/.venv", "/venv/", "/site-packages/", "/__pycache__/")
    return not any(token in p for token in blocked_tokens)


def _build_pack_next_hint(results: list[dict[str, object]]) -> str | None:
    for row in results:
        top_path = str(row.get("caller_path") or "").strip()
        if _is_next_candidate_path(top_path):
            return f"SARI_NEXT: read(mode=file,target={pack_encode_id(top_path)})"
    return None


def execute_get_callers(args: object, db: object, roots: list[str]) -> ToolResult:
    """
    특정 심볼을 호출하는 다른 심볼들을 높은 정확도로 검색합니다.
    (Symbol References / Usage Search)
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("get_callers", "args must be an object")

    target_symbol = str(args.get("name", "") or "").strip()
    target_sid = str(args.get("symbol_id", "") or "").strip() or str(args.get("sid", "") or "").strip()
    target_path = str(args.get("path", "")).strip()
    repo = str(args.get("repo", "")).strip()
    limit, err = parse_int_arg(args, "limit", 100, "get_callers", min_value=1, max_value=500)
    if err:
        return err
    if limit is None:
        return invalid_args_response("get_callers", "'limit' must be an integer")
    
    if not target_symbol and not target_sid:
        return mcp_response(
            "get_callers",
            lambda: pack_error("get_callers", ErrorCode.INVALID_ARGS, "Symbol name or symbol_id is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "Symbol name or symbol_id is required"}, "isError": True},
        )

    # 1. 유효 범위(Scope) 해결
    allowed_root_ids = resolve_root_ids(roots)
    req_root_ids = args.get("root_ids")
    if isinstance(req_root_ids, list) and req_root_ids:
        req_set = {str(x) for x in req_root_ids if x}
        effective_root_ids = [rid for rid in allowed_root_ids if rid in req_set]
    else:
        effective_root_ids = allowed_root_ids

    _, repo_root_ids = resolve_repo_scope(repo, roots, db=db)
    if repo_root_ids:
        repo_set = set(repo_root_ids)
        effective_root_ids = [rid for rid in effective_root_ids if rid in repo_set] if effective_root_ids else list(repo_root_ids)

    # 2. 쿼리 생성 - 심볼 ID 우선
    params = []
    if target_sid:
        sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol_id = ?"
        params.append(target_sid)
    else:
        # ID가 없으면 이름으로 매칭하되, 경로는 유연하게 처리
        sql = "SELECT from_path, from_symbol, from_symbol_id, line, rel_type FROM symbol_relations WHERE to_symbol = ?"
        params.append(target_symbol)
        
        if target_path:
            # 경로가 주어지면 정확히 일치하거나 경로 정보가 없는 경우 허용
            sql += " AND (to_path = ? OR to_path = '' OR to_path IS NULL)"
            params.append(target_path)

    if effective_root_ids:
        root_clause = " OR ".join(["from_path LIKE ?"] * len(effective_root_ids))
        sql += f" AND ({root_clause})"
        params.extend([f"{rid}/%" for rid in effective_root_ids])

    sql += " ORDER BY from_path, line LIMIT ?"
    params.append(limit)

    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    results = []
    try:
        rows = conn.execute(sql, params).fetchall()
        for r in rows:
            results.append(CallerHitDTO.from_row(r).model_dump())
    except Exception as e: 
        import logging
        logging.getLogger("sari.mcp.get_callers").debug("SQL query failed: %s", e)

    # 3. 직접적인 관계가 없을 경우 Call Graph(depth=1) 휴리스틱 사용 (Fallback)
    if not results:
        try:
            graph = build_call_graph(
                {"symbol": target_symbol, "symbol_id": target_sid, "path": target_path, "depth": 1},
                db,
                roots,
            )
            children = ((graph.get("upstream") or {}).get("children") or [])[:limit]
            for c in children:
                results.append({
                    "caller_path": c.get("path", ""),
                    "caller_symbol": c.get("name", ""),
                    "caller_symbol_id": c.get("symbol_id", ""),
                    "line": int(c.get("line", 0) or 0),
                    "rel_type": c.get("rel_type", "calls_heuristic"),
                })
        except Exception as e:
            import logging
            logging.getLogger("sari.mcp.get_callers").debug("Call graph fallback failed: %s", e)

    def build_pack() -> str:
        lines = [pack_header("get_callers", {"name": pack_encode_text(target_symbol), "sid": pack_encode_id(target_sid), "path": pack_encode_id(target_path), "repo": pack_encode_id(repo)}, returned=len(results))]
        for r in results:
            kv = {
                "caller_path": pack_encode_id(r["caller_path"]),
                "caller_symbol": pack_encode_id(r["caller_symbol"]),
                "caller_sid": pack_encode_id(r.get("caller_symbol_id", "")),
                "line": str(r["line"]),
                "rel_type": pack_encode_id(r["rel_type"]),
            }
            lines.append(pack_line("r", kv))
        next_line = _build_pack_next_hint(results)
        if next_line:
            lines.append(next_line)
        return "\n".join(lines)

    return mcp_response(
        "get_callers",
        build_pack,
        lambda: {"target": target_symbol, "target_sid": target_sid, "results": results, "count": len(results)},
    )
