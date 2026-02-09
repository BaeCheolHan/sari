#!/usr/bin/env python3
"""
Search tool for Local Search MCP Server (SSOT).
SSOT (Single Source of True) 원칙을 따르는 통합 검색 도구입니다.
"""
import time
from typing import Any, Dict, List

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
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, str(e)),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": str(e)}, "isError": True},
        )

    if not opts.query:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "query is required"}, "isError": True},
        )

    # 2. DB Facade를 통한 하이브리드 검색 (Tantivy vs SQLite 자동 처리)
    try:
        hits, meta = db.search_v2(opts)
    except Exception as e:
        return mcp_response(
            "search",
            lambda: pack_error("search", ErrorCode.ERR_ENGINE_QUERY, str(e)),
            lambda: {"error": {"code": ErrorCode.ERR_ENGINE_QUERY.value, "message": str(e)}, "isError": True},
        )

    latency_ms = int((time.time() - start_ts) * 1000)
    total = int(meta.get("total", len(hits)))
    
    def build_json() -> Dict[str, Any]:
        """JSON 포맷 응답 생성 (디버깅용)"""
        return {
            "query": opts.query, "limit": opts.limit, "offset": opts.offset,
            "results": [h.to_result_dict() if hasattr(h, "to_result_dict") else h for h in hits],
            "meta": {**meta, "latency_ms": latency_ms}
        }

    def build_pack() -> str:
        """PACK1 포맷 응답 생성 (토큰 절약용)"""
        returned = len(hits)
        header = pack_header("search", {"q": pack_encode_text(opts.query)}, returned=returned)
        lines = [header]
        
        # 메타데이터 라인
        lines.append(pack_line("m", {"total": str(total), "latency_ms": str(latency_ms), "engine": str(meta.get("engine", "unknown"))}))
        
        for h in hits:
            # 중요도(Importance) 정보 추출하여 시각적 태그 추가
            imp_tag = ""
            if hasattr(h, "hit_reason") and "importance=" in h.hit_reason:
                try:
                    imp_val = h.hit_reason.split("importance=")[1].split(")")[0]
                    if float(imp_val) > 10.0: imp_tag = " [CORE]"
                    elif float(imp_val) > 2.0: imp_tag = " [SIG]"
                except: pass
            
            # 딕셔너리/객체 접근을 위한 안전한 헬퍼
            def get_attr(obj, attr, default=""):
                if isinstance(obj, dict): return obj.get(attr, default)
                return getattr(obj, attr, default)

            lines.append(pack_line("r", {
                "path": pack_encode_id(get_attr(h, "path")),
                "repo": pack_encode_id(get_attr(h, "repo")),
                "score": f"{float(get_attr(h, 'score', 0.0)):.2f}",
                "file_type": pack_encode_id(get_attr(h, "file_type")),
                "snippet": pack_encode_text(get_attr(h, "snippet")),
                "rank_info": pack_encode_text(get_attr(h, "hit_reason") + imp_tag),
            }))
        if returned >= opts.limit:
            lines.append(pack_truncated(opts.offset + opts.limit, opts.limit, "maybe"))
        return "\n".join(lines)

    return mcp_response("search", build_pack, build_json)
