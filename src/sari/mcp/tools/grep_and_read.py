from typing import Mapping, TypeAlias

from sari.core.db import LocalSearchDB
from sari.core.models import SearchOptions
from sari.mcp.tools._util import (
    mcp_response,
    pack_error,
    pack_header,
    pack_line,
    pack_encode_text,
    pack_encode_id,
    ErrorCode,
    resolve_root_ids,
    require_db_schema,
)

ToolResult: TypeAlias = dict[str, object]
ToolArgs: TypeAlias = dict[str, object]
ReadRow: TypeAlias = dict[str, str]


def _normalize_query(q: object) -> str:
    """쿼리 문자열을 정규화합니다."""
    return str(q or "").strip()


def _coerce_int(val: object, default: int) -> int:
    """값을 정수형으로 변환하며, 실패 시 기본값을 반환합니다."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def execute_grep_and_read(args: object, db: LocalSearchDB, roots: list[str]) -> ToolResult:
    """
    복합 도구: 하이브리드 검색을 먼저 수행한 후, 최상위 결과 파일들의 전체 내용을 함께 읽어옵니다.
    (Search then Read top results)
    """
    if not isinstance(args, Mapping):
        return mcp_response(
            "grep_and_read",
            lambda: pack_error("grep_and_read", ErrorCode.INVALID_ARGS, "'args' must be an object"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "'args' must be an object",
                },
                "isError": True,
            },
        )
    args_map: ToolArgs = dict(args)

    guard = require_db_schema(
        db,
        "grep_and_read",
        "files",
        ["path", "rel_path", "root_id", "repo", "deleted_ts", "fts_content"],
    )
    if guard:
        return guard
    query = _normalize_query(args_map.get("query"))
    if not query:
        return mcp_response(
            "grep_and_read",
            lambda: pack_error("grep_and_read", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "query is required"}, "isError": True},
        )

    repo = args_map.get("scope") or args_map.get("repo")
    if repo == "workspace":
        repo = None

    limit = max(1, min(_coerce_int(args_map.get("limit"), 8), 50))
    read_limit = max(1, min(_coerce_int(args_map.get("read_limit"), 3), limit))

    raw_lines = _coerce_int(args_map.get("context_lines"), 5)
    snippet_lines = min(max(raw_lines, 1), 20)
    total_mode = str(args_map.get("total_mode") or "").strip().lower()
    if total_mode not in {"exact", "approx"}:
        total_mode = "exact"

    root_ids = resolve_root_ids(roots)
    req_root_ids = args_map.get("root_ids")
    if isinstance(req_root_ids, list):
        req_root_ids = [str(r) for r in req_root_ids if r]
        if root_ids:
            root_ids = [r for r in root_ids if r in req_root_ids]
        else:
            root_ids = list(req_root_ids)
        if req_root_ids and not root_ids:
            if db and db.has_legacy_paths():
                root_ids = []
            else:
                return mcp_response(
                    "grep_and_read",
                    lambda: pack_error("grep_and_read", ErrorCode.ERR_ROOT_OUT_OF_SCOPE, "root_ids out of scope", hints=["outside final_roots"]),
                    lambda: {"error": {"code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value, "message": "root_ids out of scope"}, "isError": True},
                )

    opts = SearchOptions(
        query=query,
        repo=repo,
        limit=limit,
        offset=0,
        snippet_lines=snippet_lines,
        file_types=list(args_map.get("file_types", [])),
        path_pattern=args_map.get("path_pattern"),
        exclude_patterns=args_map.get("exclude_patterns", []),
        recency_boost=bool(args_map.get("recency_boost", False)),
        use_regex=bool(args_map.get("use_regex", False)),
        case_sensitive=bool(args_map.get("case_sensitive", False)),
        total_mode=total_mode,
        root_ids=root_ids,
    )

    try:
        # 1. 하이브리드 검색 실행
        hits, meta = db.search_v2(opts)
    except Exception as exc:
        msg = str(exc)
        return mcp_response(
            "grep_and_read",
            lambda: pack_error(
                "grep_and_read",
                ErrorCode.ERR_ENGINE_QUERY,
                f"engine query failed: {msg}",
                hints=["check engine status", "run sari doctor"],
            ),
            lambda: {
                "error": {
                    "code": ErrorCode.ERR_ENGINE_QUERY.value,
                    "message": f"engine query failed: {msg}",
                    "hint": "check engine status | run sari doctor",
                },
                "isError": True,
            },
        )

    # 2. 검색 결과 중 상위 N개 파일의 실제 내용 읽기
    read_results: list[dict[str, object]] = []
    read_errors: list[ReadRow] = []
    for h in hits[:read_limit]:
        try:
            content = db.read_file(h.path)
        except Exception as exc:
            content = None
            read_errors.append({"path": h.path, "error": str(exc), "hint": "run scan_once"})
        if content is None:
            read_errors.append({"path": h.path, "error": "not indexed", "hint": "run scan_once"})
            continue
        read_results.append({"path": h.path, "content": content})

    def build_json() -> ToolResult:
        results: list[dict[str, object]] = []
        for h in hits:
            results.append(
                {
                    "path": h.path,
                    "repo": h.repo,
                    "score": h.score,
                    "snippet": h.snippet,
                    "mtime": h.mtime,
                    "size": h.size,
                    "match_count": h.match_count,
                    "file_type": h.file_type,
                    "hit_reason": h.hit_reason,
                }
            )
        return {
            "query": query,
            "limit": limit,
            "read_limit": read_limit,
            "results": results,
            "read_results": read_results,
            "read_errors": read_errors,
            "meta": {
                "total": meta.get("total", len(results)),
                "total_mode": meta.get("total_mode", total_mode),
            },
        }

    def build_pack() -> str:
        returned = len(hits)
        header = pack_header("grep_and_read", {"q": pack_encode_text(query)}, returned=returned)
        lines = [header]
        for h in hits:
            lines.append(
                pack_line(
                    "r",
                    {
                        "path": pack_encode_id(h.path),
                        "repo": pack_encode_id(h.repo),
                        "score": f"{h.score:.3f}",
                        "mtime": str(h.mtime),
                        "size": str(h.size),
                        "file_type": pack_encode_id(h.file_type),
                        "snippet": pack_encode_text(h.snippet),
                    },
                )
            )
        for r in read_results:
            lines.append(
                pack_line(
                    "f",
                    {
                        "path": pack_encode_id(r.get("path", "")),
                        "content": pack_encode_text(r.get("content", "")),
                    },
                )
            )
        for e in read_errors:
            lines.append(
                pack_line(
                    "e",
                    {
                        "path": pack_encode_id(e.get("path", "")),
                        "error": pack_encode_text(e.get("error", "")),
                        "hint": pack_encode_text(e.get("hint", "")),
                    },
                )
            )
        return "\n".join(lines)

    return mcp_response("grep_and_read", build_pack, build_json)
