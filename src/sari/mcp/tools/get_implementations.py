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
from sari.core.services.symbol_service import SymbolService

ToolResult: TypeAlias = dict[str, object]


def _is_next_candidate_path(path: str) -> bool:
    p = str(path or "").strip().lower()
    if not p:
        return False
    blocked_tokens = ("/.idea/", "/.vscode/", "/.venv", "/venv/", "/site-packages/", "/__pycache__/")
    return not any(token in p for token in blocked_tokens)


def _build_pack_next_hint(results: list[dict[str, object]]) -> str | None:
    for row in results:
        top_path = str(row.get("implementer_path") or "").strip()
        if _is_next_candidate_path(top_path):
            return f"SARI_NEXT: read(mode=file,target={pack_encode_id(top_path)})"
    return None


def execute_get_implementations(args: object, db: object, roots: list[str]) -> ToolResult:
    """
    SymbolService를 사용하여 특정 심볼을 구현(implements)하거나 상속(extends)하는 심볼들을 찾습니다.
    (Interface Implementation / Subclass Search)
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("get_implementations", "args must be an object")

    target_symbol = str(args.get("name", "")).strip()
    target_sid = str(args.get("symbol_id", "")).strip() or str(args.get("sid", "")).strip()
    target_path = str(args.get("path", "")).strip()
    repo = str(args.get("repo", "")).strip()
    limit, err = parse_int_arg(args, "limit", 100, "get_implementations", min_value=1, max_value=500)
    if err:
        return err
    if limit is None:
        return invalid_args_response("get_implementations", "'limit' must be an integer")
    
    if not target_symbol and not target_sid:
        return mcp_response(
            "get_implementations",
            lambda: pack_error("get_implementations", ErrorCode.INVALID_ARGS, "Symbol name or symbol_id is required"),
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

    # 2. 서비스 계층 위임 (순수 비즈니스 로직)
    service = SymbolService(db)
    results = service.get_implementations(
        target_name=target_symbol,
        symbol_id=target_sid,
        path=target_path,
        limit=limit,
        root_ids=effective_root_ids
    )

    def build_pack() -> str:
        header_params = {
            "name": pack_encode_text(target_symbol),
            "sid": pack_encode_id(target_sid),
            "path": pack_encode_id(target_path),
            "repo": pack_encode_id(repo)
        }
        lines = [pack_header("get_implementations", header_params, returned=len(results))]
        for r in results:
            kv = {
                "implementer_path": pack_encode_id(r["implementer_path"]),
                "implementer_symbol": pack_encode_id(r["implementer_symbol"]),
                "implementer_sid": pack_encode_id(r.get("implementer_sid", "")),
                "rel_type": pack_encode_id(r["rel_type"]),
                "line": str(r["line"]),
            }
            lines.append(pack_line("r", kv))
        next_line = _build_pack_next_hint(results)
        if next_line:
            lines.append(next_line)
        return "\n".join(lines)

    return mcp_response(
        "get_implementations",
        build_pack,
        lambda: {"target": target_symbol, "target_sid": target_sid, "results": results, "count": len(results)},
    )
