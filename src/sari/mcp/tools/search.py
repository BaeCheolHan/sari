#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
SSOT (Single Source of True) 원칙을 따르는 통합 검색 도구입니다.
"""
import time
from typing import Any, Dict, List

from sari.core.settings import settings
from sari.mcp.tools._util import (
    mcp_response,
    pack_header,
    pack_line,
    pack_truncated,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    ErrorCode,
    parse_search_options,
)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _clip_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def execute_search(
    args: Dict[str, Any],
    db: Any,
    logger: Any,
    roots: List[str],
    engine: Any = None,     # 사용되지 않지만 서명 호환성을 위해 유지
    indexer: Any = None,    # 사용되지 않지만 서명 호환성을 위해 유지
) -> Dict[str, Any]:
    """
    현대화된 Facade 패턴을 사용하여 하이브리드 검색을 실행합니다.
    Tantivy(Rust 엔진)와 SQLite 검색을 자동으로 분기 처리합니다.

    Args:
        args: MCP 클라이언트로부터 전달받은 검색 인자
        db: 데이터베이스 접근 객체 (LocalSearchDB)
        logger: 로거 객체
        roots: 검색 대상 루트 디렉토리 목록
        engine: (Deprecated) 이전 버전 호환성용
        indexer: (Deprecated) 이전 버전 호환성용

    Returns:
        MCP 응답 딕셔너리 (JSON 또는 PACK 형식)
    """
    start_ts = time.time()

    # 1. 표준화된 옵션 파싱
    try:
        opts = parse_search_options(args, roots)
    except Exception as e:
        msg = str(e)
        return mcp_response(
            "search",
            lambda: pack_error(
                "search",
                ErrorCode.INVALID_ARGS,
                msg),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": msg},
                "isError": True},
        )

    if not opts.query:
        return mcp_response(
            "search",
            lambda: pack_error(
                "search",
                ErrorCode.INVALID_ARGS,
                "query is required"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "query is required"},
                "isError": True},
        )

    # 2. DB Facade를 통한 하이브리드 검색 (Tantivy vs SQLite 자동 처리)
    try:
        hits, meta = db.search_v2(opts)
    except Exception as e:
        msg = str(e)
        return mcp_response(
            "search",
            lambda: pack_error(
                "search",
                ErrorCode.ERR_ENGINE_QUERY,
                msg),
            lambda: {
                "error": {
                    "code": ErrorCode.ERR_ENGINE_QUERY.value,
                    "message": msg},
                "isError": True},
        )

    latency_ms = int((time.time() - start_ts) * 1000)
    total = int(meta.get("total", len(hits)))
    max_results = max(
        1, min(
            _safe_int(
                args.get("max_results"), opts.limit), opts.limit))
    snippet_max_chars = max(
        80, min(
            _safe_int(
                args.get("snippet_max_chars"), getattr(
                    settings, "MCP_SEARCH_SNIPPET_MAX_CHARS", 700)), 2000))
    pack_max_bytes = max(
        4096,
        _safe_int(
            args.get("max_pack_bytes"),
            getattr(
                settings,
                "MCP_SEARCH_PACK_MAX_BYTES",
                120000)))
    bounded_hits = hits[:max_results]

    def get_attr(obj: Any, attr: str, default: Any = "") -> Any:
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def build_json() -> Dict[str, Any]:
        """JSON 포맷 응답 생성 (디버깅용)"""
        json_results = []
        for item in bounded_hits:
            if hasattr(item, "to_result_dict"):
                row = item.to_result_dict()
            elif isinstance(item, dict):
                row = dict(item)
            else:
                row = {
                    "path": str(get_attr(item, "path", "")),
                    "repo": str(get_attr(item, "repo", "")),
                    "score": float(get_attr(item, "score", 0.0)),
                    "file_type": str(get_attr(item, "file_type", "")),
                    "snippet": str(get_attr(item, "snippet", "")),
                    "hit_reason": str(get_attr(item, "hit_reason", "")),
                }
            row["snippet"] = _clip_text(
                row.get("snippet", ""), snippet_max_chars)
            json_results.append(row)
        return {
            "query": opts.query, "limit": opts.limit, "offset": opts.offset,
            "results": json_results,
            "meta": {
                **meta,
                "latency_ms": latency_ms,
                "returned": len(json_results),
                "bounded_by_max_results": len(hits) > len(json_results),
                "snippet_max_chars": snippet_max_chars,
            },
        }

    def build_pack() -> str:
        """PACK1 포맷 응답 생성 (토큰 절약용)"""
        header = pack_header("search",
                             {"q": pack_encode_text(opts.query)},
                             returned=len(bounded_hits))
        lines = [header]
        used_bytes = len(header.encode("utf-8", errors="ignore")) + 1

        # 메타데이터 라인
        meta_line = pack_line("m", {"total": str(total), "latency_ms": str(
            latency_ms), "engine": str(meta.get("engine", "unknown"))})
        lines.append(meta_line)
        used_bytes += len(meta_line.encode("utf-8", errors="ignore")) + 1

        returned_count = 0
        hard_truncated = False
        for item in bounded_hits:
            # 중요도(Importance) 정보 추출하여 시각적 태그 추가
            imp_tag = ""
            hit_reason = str(get_attr(item, "hit_reason", ""))
            if "importance=" in hit_reason:
                try:
                    imp_val = hit_reason.split("importance=")[1].split(")")[0]
                    if float(imp_val) > 10.0:
                        imp_tag = " [CORE]"
                    elif float(imp_val) > 2.0:
                        imp_tag = " [SIG]"
                except Exception:
                    pass

            snippet = _clip_text(get_attr(item, "snippet"), snippet_max_chars)
            row_line = pack_line("r", {
                "path": pack_encode_id(get_attr(item, "path")),
                "repo": pack_encode_id(get_attr(item, "repo")),
                "score": f"{float(get_attr(item, 'score', 0.0)):.2f}",
                "file_type": pack_encode_id(get_attr(item, "file_type")),
                "snippet": pack_encode_text(snippet),
                "rank_info": pack_encode_text(hit_reason + imp_tag),
            })
            row_bytes = len(row_line.encode("utf-8", errors="ignore")) + 1
            if used_bytes + row_bytes > pack_max_bytes:
                hard_truncated = True
                break

            lines.append(row_line)
            used_bytes += row_bytes
            returned_count += 1

        soft_truncated = len(hits) > returned_count
        if hard_truncated or soft_truncated:
            next_offset = opts.offset + max(returned_count, 1)
            lines.append(pack_truncated(next_offset, opts.limit, "maybe"))
            lines.append(
                pack_line(
                    "m", {
                        "budget_bytes": str(pack_max_bytes), "returned": str(returned_count)}))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)
