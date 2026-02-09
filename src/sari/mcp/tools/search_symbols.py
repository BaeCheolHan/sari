import json
from typing import Any, Dict, List, Optional
from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    resolve_root_ids,
    pack_error,
    ErrorCode,
)
from sari.core.services.symbol_service import SymbolService

def execute_search_symbols(args: Dict[str, Any], db: Any, logger: Any, roots: List[str]) -> Dict[str, Any]:
    """
    스마트 랭킹을 적용한 코드 심볼(클래스, 함수 등) 검색 도구입니다.
    쿼리에 일치하는 심볼을 찾아 중요도 순으로 정렬하여 반환합니다.
    """
    query = args.get("query", "").strip()
    if not query:
        return mcp_response(
            "search_symbols",
            lambda: pack_error("search_symbols", ErrorCode.INVALID_ARGS, "Query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "Query is required"}, "isError": True},
        )

    limit = max(1, min(int(args.get("limit", 20) or 20), 100))
    repo = args.get("repo")
    kinds = args.get("kinds")
    
    # 1. 범위(Scope) 해석
    root_ids = resolve_root_ids(roots)
    req_root_ids = args.get("root_ids")
    if isinstance(req_root_ids, list) and req_root_ids:
        # 요청된 root_id가 유효한 범위 내에 있는지 확인
        req_set = {str(x) for x in req_root_ids}
        root_ids = [rid for rid in root_ids if rid in req_set]

    # 2. 서비스 계층을 통한 검색 실행
    svc = SymbolService(db)
    results = svc.search(
        query=query,
        limit=limit,
        root_ids=root_ids,
        repo=repo,
        kinds=kinds
    )

    def build_pack() -> str:
        header_params = {
            "q": pack_encode_text(query),
            "limit": str(limit),
            "repo": pack_encode_id(repo or "")
        }
        lines = [pack_header("search_symbols", header_params, returned=len(results))]
        for s in results:
            kv = {
                "name": pack_encode_id(s.name),
                "kind": pack_encode_id(s.kind),
                "path": pack_encode_id(s.path),
                "line": str(s.line),
                "qualname": pack_encode_id(s.qualname),
                "repo": pack_encode_id(s.repo or ""),
                "sid": pack_encode_id(s.symbol_id),
            }
            lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response(
        "search_symbols",
        build_pack,
        lambda: {"query": query, "results": [s.model_dump() for s in results], "count": len(results)},
    )
