#!/usr/bin/env python3
"""
로컬 검색 MCP 서버를 위한 재스캔(Rescan) 도구.
인덱싱 프로세스를 비동기로 트리거합니다.
"""
from typing import Any, Dict
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_error, ErrorCode
from sari.core.indexer import Indexer
from sari.core.services.index_service import IndexService


def execute_rescan(args: Dict[str, Any], indexer: Indexer) -> Dict[str, Any]:
    """
    Indexer에게 비동기 재스캔(Rescan) 작업을 요청합니다.
    작업은 백그라운드에서 수행됩니다.
    """
    svc = IndexService(indexer)
    result = svc.rescan()
    if not result.get("ok"):
        code = result.get("code", ErrorCode.INTERNAL)
        message = result.get("message", "indexer not available")
        data = result.get("data")
        return mcp_response(
            "rescan",
            lambda: pack_error("rescan", code, message, fields=data),
            lambda: {"error": {"code": code.value, "message": message, "data": data}, "isError": True},
        )

    def build_json() -> Dict[str, Any]:
        return {"requested": True}

    def build_pack() -> str:
        lines = [pack_header("rescan", {}, returned=1)]
        lines.append(pack_line("m", kv={"requested": "true"}))
        return "\n".join(lines)

    return mcp_response("rescan", build_pack, build_json)
