from collections.abc import Mapping
import os
from typing import TypeAlias

from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_encode_id,
    pack_encode_text,
    resolve_root_ids,
    pack_error,
    ErrorCode,
    parse_int_arg,
    require_repo_arg,
    invalid_args_response,
)
from sari.core.services.symbol_service import SymbolService
from sari.mcp.tools._symbol_hydration import hydrate_symbols_for_search

ToolResult: TypeAlias = dict[str, object]


def execute_search_symbols(args: object, db: object, logger: object, roots: list[str]) -> ToolResult:
    """
    스마트 랭킹을 적용한 코드 심볼(클래스, 함수 등) 검색 도구입니다.
    쿼리에 일치하는 심볼을 찾아 중요도 순으로 정렬하여 반환합니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("search_symbols", "args must be an object")
    enforce_repo = bool(args.get("__enforce_repo__", str(os.environ.get("SARI_FORMAT", "pack")).strip().lower() == "pack"))
    if enforce_repo:
        repo_err = require_repo_arg(args, "search_symbols")
        if repo_err:
            return repo_err

    query = str(args.get("query", "")).strip()
    if not query:
        return mcp_response(
            "search_symbols",
            lambda: pack_error("search_symbols", ErrorCode.INVALID_ARGS, "Query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "Query is required"}, "isError": True},
        )

    limit, err = parse_int_arg(args, "limit", 20, "search_symbols", min_value=1, max_value=100)
    if err:
        return err
    if limit is None:
        return invalid_args_response("search_symbols", "'limit' must be an integer")
    repo = str(args.get("repo", "")).strip()
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

    if not results:
        hydrate_symbols_for_search(
            db=db,
            roots=roots,
            repo=repo,
            query=query,
            max_files=max(4, min(limit, 24)),
        )
        results = svc.search(
            query=query,
            limit=limit,
            root_ids=root_ids,
            repo=repo,
            kinds=kinds,
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
