#!/usr/bin/env python3
"""
Sari MCP 서버를 위한 저장소 후보 추천 도구.
검색 쿼리와 가장 관련성이 높은 저장소(Repo)들을 찾아 추천 이유와 함께 반환합니다.
"""
import json
from typing import Any, Dict, List
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_id, pack_encode_text, pack_error, ErrorCode, resolve_root_ids, require_db_schema

from sari.core.db import LocalSearchDB
from sari.mcp.telemetry import TelemetryLogger


def execute_repo_candidates(args: Dict[str, Any], db: LocalSearchDB, logger: TelemetryLogger = None, roots: List[str] = None) -> Dict[str, Any]:
    """
    쿼리와 일치하는 파일이 많은 리포지토리를 찾아 후보군을 제안합니다.
    어떤 리포지토리에서 작업을 시작해야 할지 모를 때 유용합니다.
    """
    guard = require_db_schema(
        db,
        "repo_candidates",
        "files",
        ["path", "rel_path", "root_id", "repo", "deleted_ts", "fts_content"],
    )
    if guard:
        return guard
        
    query = args.get("query", "")
    try:
        limit_arg = min(int(args.get("limit", 3)), 5)
    except (ValueError, TypeError):
        limit_arg = 3

    if not query.strip():
        return mcp_response(
            "repo_candidates",
            lambda: pack_error("repo_candidates", ErrorCode.INVALID_ARGS, "query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "query is required"}, "isError": True},
        )

    def get_candidates():
        """관련 리포지토리 후보군을 계산하고 추천 이유를 생성합니다."""
        root_ids = resolve_root_ids(list(roots or []))
        candidates = db.repo_candidates(q=query, limit=limit_arg, root_ids=root_ids)
        for candidate in candidates:
            score = candidate.get("score", 0)
            if score >= 10:
                reason = f"High match ({score} files contain '{query}')"
            elif score >= 5:
                reason = f"Medium match ({score} files)"
            else:
                reason = f"Low match ({score} files)"
            candidate["reason"] = reason
        return candidates

    # --- JSON Builder ---
    def build_json() -> Dict[str, Any]:
        """JSON 형식의 응답을 생성합니다."""
        candidates = get_candidates()
        return {
            "query": query,
            "candidates": candidates,
            "hint": "After selecting, use the 'repo' parameter in search tools to narrow down.",
        }

    # --- PACK1 Builder ---
    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        candidates = get_candidates()

        # 헤더 생성
        kv = {"q": pack_encode_text(query), "limit": limit_arg}
        lines = [
            pack_header("repo_candidates", kv, returned=len(candidates))
        ]

        # 개별 레코드 행 생성
        for c in candidates:
            # r:repo=<repo> score=<score> reason=<reason>
            kv_line = {
                "repo": pack_encode_id(c["repo"]),
                "score": str(c["score"]),
                "reason": pack_encode_text(c["reason"])
            }
            lines.append(pack_line("r", kv_line))

        return "\n".join(lines)

    if logger and hasattr(logger, "log_telemetry"):
        logger.log_telemetry(f"tool=repo_candidates query='{query}' limit={limit_arg}")

    return mcp_response("repo_candidates", build_pack, build_json)
