import os
from pathlib import Path
from typing import Any, Dict, List
from sari.core.db import LocalSearchDB
from sari.mcp.tools._util import mcp_response, pack_error, ErrorCode, resolve_db_path, pack_header, pack_line, pack_encode_text

def execute_read_file(args: Dict[str, Any], db: LocalSearchDB, roots: List[str]) -> Dict[str, Any]:
    """
    파일 내용을 읽어오는 도구입니다. 대용량 파일의 경우 페이지네이션을 지원합니다.
    검색(search)이나 심볼 목록(list_symbols) 조회 후 사용하는 것이 좋습니다.

    Args:
        args: {"path": str, "offset": int, "limit": int} 형태의 인자
        db: LocalSearchDB 인스턴스
    """
    # 인자 검증 및 파싱
    validation_result = _validate_read_file_args(args)
    if validation_result:
        return validation_result
    
    path = args["path"]
    offset = int(args.get("offset", 0))
    limit = int(args["limit"]) if args.get("limit") is not None else None
    
    # DB 경로 변환 및 파일 읽기
    # 정책 업데이트: 이제 resolve_db_path는 DB를 직접 조회하여 더 넓은 범위를 허용합니다.
    db_path = resolve_db_path(path, roots, db=db)
    
    if not db_path:
        # 1단계: 디스크 존재 여부 확인
        p_abs = Path(os.path.expanduser(path)).resolve()
        if p_abs.exists() and p_abs.is_file():
            # 디스크엔 있지만 수집되지 않은 경우 -> 등록 가이드 제공
            suggested_root = str(p_abs.parent)
            msg = (
                f"파일이 존재하지만 현재 분석 범위(인덱스)에 포함되어 있지 않습니다. "
                f"이 파일을 분석하려면 'sari.json'이나 MCP 설정의 'roots'에 '{suggested_root}' "
                f"또는 상위 프로젝트 경로를 추가하여 수집되도록 설정해 주세요."
            )
            return mcp_response(
                "read_file",
                lambda: pack_error("read_file", ErrorCode.ERR_ROOT_OUT_OF_SCOPE, msg),
                lambda: {"error": {"code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value, "message": msg}, "isError": True},
            )
        else:
            # 디스크에도 없는 경우 -> 일반적인 미존재 에러
            msg = f"파일을 찾을 수 없습니다: {path}. 경로가 정확한지 확인해 주세요."
            return mcp_response(
                "read_file",
                lambda: pack_error("read_file", ErrorCode.NOT_INDEXED, msg),
                lambda: {"error": {"code": ErrorCode.NOT_INDEXED.value, "message": msg}, "isError": True},
            )
    
    # DB 경로가 있는 경우 (인덱스 히트)
    read_result = _read_file_content(db, db_path, path)
    if read_result.get("error"):
        return read_result["error"]
    
    content = read_result["content"]
    
    # 페이지네이션 적용 (부분 읽기)
    pagination_result = _apply_pagination(content, offset, limit)
    
    # 효율성 지표를 위한 토큰 수 계산
    token_count = _count_tokens(pagination_result["content"])
    
    # 응답 생성
    return _build_read_file_response(
        pagination_result["content"],
        offset,
        limit,
        pagination_result["total_lines"],
        pagination_result["is_truncated"],
        pagination_result.get("next_offset"),
        token_count
    )


def _validate_read_file_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """read_file 인자의 유효성을 검사합니다."""
    path = args.get("path")
    if not path:
        return mcp_response(
            "read_file",
            lambda: pack_error("read_file", ErrorCode.INVALID_ARGS, "'path' is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "'path' is required"}, "isError": True},
        )
    return None


def _read_file_content(db: LocalSearchDB, db_path: str, original_path: str) -> Dict[str, Any]:
    """데이터베이스에서 파일 내용을 읽어옵니다."""
    if not db_path:
        return {
            "error": mcp_response(
                "read_file",
                lambda: pack_error("read_file", ErrorCode.ERR_ROOT_OUT_OF_SCOPE, f"Path out of scope: {original_path}", hints=["outside final_roots"]),
                lambda: {"error": {"code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value, "message": f"Path out of scope: {original_path}"}, "isError": True},
            )
        }
    
    content = db.read_file(db_path)
    if content is None:
        return {
            "error": mcp_response(
                "read_file",
                lambda: pack_error(
                    "read_file",
                    ErrorCode.NOT_INDEXED,
                    f"File not found or not indexed: {db_path}",
                    hints=["run scan_once", "verify path with search"],
                ),
                lambda: {
                    "error": {
                        "code": ErrorCode.NOT_INDEXED.value,
                        "message": f"File not found or not indexed: {db_path}",
                        "hint": "run scan_once | verify path with search",
                    },
                    "isError": True,
                },
            )
        }
    
    return {"content": content}


def _apply_pagination(content: str, offset: int, limit: int = None) -> Dict[str, Any]:
    """내용에 라인 기반 페이지네이션을 적용합니다."""
    lines = content.splitlines()
    total_lines = len(lines)
    
    if limit is not None:
        end = offset + limit
        paged_lines = lines[offset:end]
        is_truncated = end < total_lines
        next_offset = offset + len(paged_lines) if is_truncated else None
    else:
        paged_lines = lines[offset:]
        is_truncated = False
        next_offset = None
    
    return {
        "content": "\n".join(paged_lines),
        "total_lines": total_lines,
        "is_truncated": is_truncated,
        "next_offset": next_offset
    }


def _count_tokens(content: str) -> int:
    """효율성 지표를 위해 텍스트의 토큰 수를 계산합니다 (근사치 사용 가능)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(content))
    except Exception:
        return len(content) // 4  # tiktoken 없을 시 글자 수 기반 근사치 사용


def _build_read_file_response(
    content: str,
    offset: int,
    limit: int,
    total_lines: int,
    is_truncated: bool,
    next_offset: int,
    token_count: int
) -> Dict[str, Any]:
    """메타데이터를 포함한 read_file 응답을 생성합니다."""
    def build_pack() -> str:
        # 헤더에 페이지네이션 및 토큰 정보 포함
        kv = {"offset": offset, "total_lines": total_lines, "tokens": token_count}
        if limit is not None:
            kv["limit"] = limit
        if is_truncated:
            kv["truncated"] = "true"
            kv["next_offset"] = next_offset
        if token_count > 2000:
            kv["warning"] = "High token usage. Consider using list_symbols or read_symbol."

        lines_out = [pack_header("read_file", kv, returned=1)]
        # 다른 도구 및 테스트와의 일관성을 위해 인코딩된 텍스트 사용
        lines_out.append(f"t:{pack_encode_text(content)}")
        return "\n".join(lines_out)

    return mcp_response(
        "read_file",
        build_pack,
        lambda: {
            "content": [{"type": "text", "text": content}],
            "metadata": {
                "offset": offset,
                "limit": limit,
                "total_lines": total_lines,
                "is_truncated": is_truncated,
                "token_count": token_count,
                "efficiency_warning": "High token usage" if token_count > 2000 else None
            }
        },
    )
