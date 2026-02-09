#!/usr/bin/env python3
"""
로컬 검색 MCP 서버를 위한 Scan-Once 도구.
동기적으로(synchronously) 한 번의 스캔 작업을 즉시 실행합니다.
"""
from typing import Any, Dict
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_error, ErrorCode
from sari.core.indexer import Indexer
from sari.core.services.index_service import IndexService


def execute_scan_once(args: Dict[str, Any], indexer: Indexer, logger: Any) -> Dict[str, Any]:
    """
    동기적(synchronous) 스캔 작업을 1회 실행하고 결과를 반환합니다.
    (One-off Scan Execution)
    """
    svc = IndexService(indexer)
    result = svc.scan_once()
    if not result.get("ok"):
        code = result.get("code", ErrorCode.INTERNAL)
        message = result.get("message", "indexer not available")
        data = result.get("data")
        return mcp_response(
            "scan_once",
            lambda: pack_error("scan_once", code, message, fields=data),
            lambda: {"error": {"code": code.value, "message": message, "data": data}, "isError": True},
        )

    scanned = int(result.get("scanned_files", 0) or 0)
    indexed = int(result.get("indexed_files", 0) or 0)

    def build_json() -> Dict[str, Any]:
        return {"ok": True, "scanned_files": scanned, "indexed_files": indexed}

    def build_pack() -> str:
        lines = [pack_header("scan_once", {}, returned=1)]
        lines.append(pack_line("m", kv={"ok": "true"}))
        lines.append(pack_line("m", kv={"scanned_files": str(scanned)}))
        lines.append(pack_line("m", kv={"indexed_files": str(indexed)}))
        return "\n".join(lines)

    return mcp_response("scan_once", build_pack, build_json)
