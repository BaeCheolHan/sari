import os
from pathlib import Path
from typing import Any, Dict, List
from .protocol import mcp_response, pack_error, ErrorCode


def handle_db_path_error(tool_name: str, path: str,
                         roots: List[str], db: Any) -> Dict[str, Any]:
    """
    파일 미존재 시 지능적 에러 가이드를 제공합니다.
    """
    try:
        p_abs = Path(os.path.expanduser(path)).resolve()
        if p_abs.exists() and p_abs.is_file():
            suggested_root = str(p_abs.parent)
            msg = (
                f"파일이 존재하지만 현재 분석 범위(인덱스)에 포함되어 있지 않습니다. "
                f"이 파일을 분석하려면 'sari.json'이나 MCP 설정의 'roots'에 '{suggested_root}' "
                f"또는 상위 프로젝트 경로를 추가하여 수집되도록 설정해 주세요.")
            return mcp_response(
                tool_name,
                lambda: pack_error(
                    tool_name,
                    ErrorCode.ERR_ROOT_OUT_OF_SCOPE,
                    msg),
                lambda: {
                    "error": {
                        "code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value,
                        "message": msg},
                    "isError": True},
            )
    except Exception:
        pass

    msg = f"파일을 찾을 수 없거나 인덱싱되지 않았습니다: {path}. 경로가 정확한지 확인해 주세요."
    return mcp_response(
        tool_name,
        lambda: pack_error(
            tool_name,
            ErrorCode.NOT_INDEXED,
            msg),
        lambda: {
            "error": {
                "code": ErrorCode.NOT_INDEXED.value,
                "message": msg},
            "isError": True},
    )


def require_db_schema(db: Any, tool: str, table: str, columns: List[str]):
    checker = getattr(db, "has_table_columns", None)
    if not checker:
        return None
    try:
        ok, missing = checker(table, columns)
        if ok:
            return None
        msg = f"DB schema mismatch: {table} missing columns: {', '.join(missing)}"
        return mcp_response(
            tool,
            lambda: pack_error(
                tool,
                ErrorCode.DB_ERROR,
                msg),
            lambda: {
                "error": {
                    "code": ErrorCode.DB_ERROR.value,
                    "message": msg},
                "isError": True})
    except Exception:
        return None
