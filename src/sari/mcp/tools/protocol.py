import urllib.parse
from typing import Any, Dict, Optional, List
from enum import Enum

class ErrorCode(str, Enum):
    """MCP 도구 실행 중 발생할 수 있는 에러 코드 정의"""
    INVALID_ARGS = "INVALID_ARGS"
    NOT_INDEXED = "NOT_INDEXED"
    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    IO_ERROR = "IO_ERROR"
    DB_ERROR = "DB_ERROR"
    INTERNAL = "INTERNAL"
    ERR_INDEXER_FOLLOWER = "ERR_INDEXER_FOLLOWER"
    ERR_INDEXER_DISABLED = "ERR_INDEXER_DISABLED"
    ERR_ROOT_OUT_OF_SCOPE = "ERR_ROOT_OUT_OF_SCOPE"
    ERR_MCP_HTTP_UNSUPPORTED = "ERR_MCP_HTTP_UNSUPPORTED"
    ERR_ENGINE_NOT_INSTALLED = "ERR_ENGINE_NOT_INSTALLED"
    ERR_ENGINE_INIT = "ERR_ENGINE_INIT"
    ERR_ENGINE_QUERY = "ERR_ENGINE_QUERY"
    ERR_ENGINE_INDEX = "ERR_ENGINE_INDEX"
    ERR_ENGINE_UNAVAILABLE = "ERR_ENGINE_UNAVAILABLE"
    ERR_ENGINE_REBUILD = "ERR_ENGINE_REBUILD"

def pack_encode_text(s: Any) -> str:
    """텍스트 데이터를 PACK1 형식의 값으로 안전하게 인코딩합니다 (URL quote)."""
    return urllib.parse.quote(str(s), safe="")

def pack_encode_id(s: Any) -> str:
    """ID나 경로 데이터를 PACK1 형식의 값으로 안전하게 인코딩합니다 (일부 특수문자 허용)."""
    return urllib.parse.quote(str(s), safe="/._-:@")

def pack_header(tool: str, kv: Dict[str, Any], returned: Optional[int] = None,
                total: Optional[int] = None, total_mode: Optional[str] = None) -> str:
    """PACK1 응답의 헤더 라인을 생성합니다."""
    parts = ["PACK1", f"tool={tool}", "ok=true"]
    for k, v in kv.items():
        parts.append(f"{k}={v}")
    if returned is not None:
        parts.append(f"returned={returned}")
    if total_mode:
        parts.append(f"total_mode={total_mode}")
    if total is not None and total_mode != "none":
        parts.append(f"total={total}")
    return " ".join(parts)

def pack_line(kind: str, kv: Optional[Dict[str, str]] = None, single_value: Optional[str] = None) -> str:
    """PACK1 응답의 개별 데이터 라인(Record, Metadata 등)을 생성합니다."""
    if single_value is not None:
        return f"{kind}:{single_value}"
    if kv:
        field_strs = [f"{k}={v}" for k, v in kv.items()]
        return f"{kind}:{ ' '.join(field_strs) }"
    return f"{kind}:"

def pack_error(tool: str, code: Any, msg: str, hints: List[str] = None, trace: str = None, fields: Dict[str, Any] = None) -> str:
    """에러 정보를 담은 PACK1 응답 전체를 생성합니다."""
    parts = [
        "PACK1",
        f"tool={tool}",
        "ok=false",
        f"code={code.value if isinstance(code, Enum) else str(code)}",
        f"msg={pack_encode_text(msg)}",
    ]
    if hints:
        parts.append(f"hint={pack_encode_text(' | '.join(hints))}")
    if trace:
        parts.append(f"trace={pack_encode_text(trace)}")
    if fields:
        for k, v in fields.items():
            parts.append(f"{k}={pack_encode_text(v)}")
    return " ".join(parts)

def pack_truncated(next_offset: int, limit: int, truncated_state: str) -> str:
    """결과가 잘렸을 경우(Pagination)를 위한 메타데이터 라인을 생성합니다."""
    return f"m:truncated={truncated_state} next=use_offset offset={next_offset} limit={limit}"
