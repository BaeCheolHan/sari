#!/usr/bin/env python3
"""
심볼 읽기 도구 (Mcp Tool).
특정 심볼(함수/클래스)의 코드 블록만 선택적으로 읽습니다.
"""
import json
import time
from collections.abc import Mapping
from typing import TypeAlias

from sari.core.db import LocalSearchDB
from sari.mcp.telemetry import TelemetryLogger
from sari.mcp.tools._util import (
    mcp_response,
    pack_error,
    ErrorCode,
    resolve_db_path,
    handle_db_path_error,
    resolve_root_ids,
    pack_header,
    pack_line,
    pack_encode_id,
    invalid_args_response,
)

ToolResult: TypeAlias = dict[str, object]


def _extract_block_from_lines(
        content: str,
        start_line: int,
        end_line: int) -> str:
    """전체 파일 내용에서 지정된 라인 범위의 코드 블록을 추출합니다."""
    lines = content.splitlines()
    if not lines:
        return ""
    s = max(1, int(start_line or 1))
    e = max(s, int(end_line or s))
    if s > len(lines):
        return ""
    return "\n".join(lines[s - 1: min(e, len(lines))])


def _symbol_candidates(
    db: LocalSearchDB,
    name: str,
    symbol_id: str,
    db_path: str | None,
    roots: list[str],
    limit: int = 50,
) -> list[dict[str, object]]:
    """
    주어진 조건(이름, ID, 경로)에 맞는 심볼 후보들을 검색합니다.
    동일한 이름의 심볼이 여러 파일에 존재할 수 있으므로 후보 목록을 반환합니다.
    """
    conn = db.get_read_connection() if hasattr(
        db, "get_read_connection") else db._read
    params: list[object] = []
    sql = "SELECT symbol_id, path, name, kind, line, end_line, qualname FROM symbols WHERE 1=1"
    if symbol_id:
        sql += " AND symbol_id = ?"
        params.append(symbol_id)
    elif name:
        sql += " AND name = ?"
        params.append(name)
    if db_path:
        sql += " AND path = ?"
        params.append(db_path)
    root_ids = resolve_root_ids(roots)
    if root_ids:
        sql += " AND (" + " OR ".join(["path LIKE ?"] * len(root_ids)) + ")"
        params.extend([f"{rid}/%" for rid in root_ids])
    sql += " ORDER BY path, line LIMIT ?"
    params.append(max(1, min(int(limit or 50), 200)))
    rows = conn.execute(sql, params).fetchall()
    cols = [
        "symbol_id",
        "path",
        "name",
        "kind",
        "line",
        "end_line",
        "qualname"]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def execute_read_symbol(args: object,
                        db: LocalSearchDB,
                        logger: TelemetryLogger,
                        roots: list[str]) -> ToolResult:
    """
    read_symbol 도구 실행 핸들러.
    심볼 이름이나 ID를 받아 해당 코드 블록을 찾아 반환합니다.
    여러 후보가 발견되면 모호성 해결을 위해 후보 목록을 반환합니다.
    """
    start_ts = time.time()
    if not isinstance(args, Mapping):
        return invalid_args_response("read_symbol", "args must be an object")

    path = str(args.get("path") or "").strip() or None
    symbol_name = str(args.get("name") or "").strip()
    symbol_id = str(args.get("symbol_id") or args.get("sid") or "").strip()

    if not symbol_name and not symbol_id:
        return mcp_response(
            "read_symbol",
            lambda: pack_error(
                "read_symbol",
                ErrorCode.INVALID_ARGS,
                "'name' or 'symbol_id' is required."),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "'name' or 'symbol_id' is required."},
                "isError": True},
        )

    db_path = resolve_db_path(path, roots, db=db) if path else None
    if path and not db_path:
        return handle_db_path_error("read_symbol", path, roots, db)

    candidates = _symbol_candidates(
        db,
        symbol_name,
        symbol_id,
        db_path,
        roots,
        limit=args.get(
            "limit",
            50))
    if not candidates:
        return mcp_response(
            "read_symbol",
            lambda: pack_error(
                "read_symbol",
                ErrorCode.NOT_INDEXED,
                "Symbol not found in current index."),
            lambda: {
                "error": {
                    "code": ErrorCode.NOT_INDEXED.value,
                    "message": "Symbol not found in current index."},
                "isError": True},
        )

    # 후보가 여러 개이고 특정 파일/ID 지정이 없는 경우: 후보 목록 반환 (Disambiguation)
    if len(candidates) > 1 and not db_path and not symbol_id:
        preview = candidates[:20]

        def build_pack_multi() -> str:
            lines = [
                pack_header(
                    "read_symbol", {
                        "name": pack_encode_id(symbol_name)}, returned=len(preview))]
            lines.append(
                pack_line(
                    "m", {
                        "needs_disambiguation": "true", "count": str(
                            len(candidates))}))
            for c in preview:
                lines.append(
                    pack_line(
                        "r",
                        {
                            "sid": pack_encode_id(c.get("symbol_id", "")),
                            "name": pack_encode_id(c.get("name", "")),
                            "kind": pack_encode_id(c.get("kind", "")),
                            "path": pack_encode_id(c.get("path", "")),
                            "line": str(c.get("line", 0)),
                            "qual": pack_encode_id(c.get("qualname", "")),
                        },
                    )
                )
            return "\n".join(lines)

        return mcp_response(
            "read_symbol",
            build_pack_multi,
            lambda: {
                "needs_disambiguation": True,
                "count": len(candidates),
                "candidates": candidates},
        )

    target = candidates[0]
    target_path = str(target.get("path", ""))
    start_line = int(target.get("line", 0) or 0)
    end_line = int(target.get("end_line", 0) or start_line)
    full_content = db.read_file(target_path) or ""
    block = _extract_block_from_lines(full_content, start_line, end_line)
    if not block:
        # 라인 정보가 부정확할 경우를 대비해 스니펫 조회 시도 (Fallback)
        block = db.get_symbol_block(
            target_path, str(
                target.get(
                    "name", symbol_name))) or ""

    latency_ms = int((time.time() - start_ts) * 1000)
    if logger and hasattr(logger, "log_telemetry"):
        logger.log_telemetry(
            f"tool=read_symbol path='{target_path}' name='{target.get('name', symbol_name)}' sid='{target.get('symbol_id', symbol_id)}' found={bool(block)} latency={latency_ms}ms"
        )

    if not block:
        return mcp_response(
            "read_symbol",
            lambda: pack_error(
                "read_symbol",
                ErrorCode.NOT_INDEXED,
                "Symbol range exists but content block extraction failed."),
            lambda: {
                "error": {
                    "code": ErrorCode.NOT_INDEXED.value,
                    "message": "Symbol range exists but content block extraction failed."},
                "isError": True},
        )

    block_dict = {
        "name": str(target.get("name", symbol_name)),
        "path": target_path,
        "start_line": start_line,
        "end_line": end_line,
        "content": block,
        "docstring": "",
        "metadata": "{}",
        "symbol_id": str(target.get("symbol_id", symbol_id)),
        "kind": str(target.get("kind", "")),
        "qualname": str(target.get("qualname", "")),
    }

    # 1. Summary Mode Extraction (요약 모드)
    # outline=True 또는 summary=True 일 때 구현부를 생략하고 시그니처와 독스트링만 반환
    summary_mode = bool(args.get("summary") or args.get("outline", False))

    doc = block_dict.get("docstring", "")
    meta = block_dict.get("metadata", "{}")
    content = str(block_dict.get("content", ""))

    if summary_mode:
        # 최적화: 첫 줄(시그니처) + 독스트링만 포함
        lines = content.splitlines()
        sig = lines[0].strip() if lines else "[empty]"
        content = f"{sig}\n{doc}\n... [implementation omitted for token optimization]"

    def build_pack() -> str:
        # Phase 11을 위한 깔끔한 Raw 데이터 포맷 사용
        kv = {
            "sid": pack_encode_id(block_dict.get("symbol_id", "")),
            "path": pack_encode_id(block_dict.get("path", "")),
            "line": block_dict.get("start_line", 0),
            "kind": pack_encode_id(block_dict.get("kind", "")),
            "tokens": (len(content) // 4)
        }
        if summary_mode:
            kv["mode"] = "summary"

        lines_out = [pack_header("read_symbol", kv, returned=1)]
        lines_out.append(pack_line("s", {
            "name": pack_encode_id(block_dict.get("name", symbol_name)),
            "qual": pack_encode_id(block_dict.get("qualname", "")),
        }))
        # 본문 데이터 (Raw Body)
        lines_out.append("b:")
        lines_out.append(content)
        return "\n".join(lines_out)

    try:
        meta_json = json.loads(meta) if isinstance(
            meta, str) and meta else meta
    except Exception:
        meta_json = {}

    return mcp_response(
        "read_symbol",
        build_pack,
        lambda: {
            "path": target_path,
            "name": block_dict.get("name", symbol_name),
            "symbol_id": block_dict.get("symbol_id", ""),
            "kind": block_dict.get("kind", ""),
            "qualname": block_dict.get("qualname", ""),
            "start_line": block_dict.get("start_line", 0),
            "end_line": block_dict.get("end_line", 0),
            "content": content,
            "docstring": doc,
            "metadata": meta_json,
            "summary_mode": summary_mode
        },
    )
