import urllib.parse
from typing import Any, Dict, Optional, List
from enum import Enum

class ErrorCode(str, Enum):
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
    return urllib.parse.quote(str(s), safe="")

def pack_encode_id(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="/._-:@")

def pack_header(tool: str, kv: Dict[str, Any], returned: Optional[int] = None,
                total: Optional[int] = None, total_mode: Optional[str] = None) -> str:
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
    if single_value is not None:
        return f"{kind}:{single_value}"
    if kv:
        field_strs = [f"{k}={v}" for k, v in kv.items()]
        return f"{kind}:{ ' '.join(field_strs) }"
    return f"{kind}:"

def pack_error(tool: str, code: Any, msg: str, hints: List[str] = None, trace: str = None, fields: Dict[str, Any] = None) -> str:
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
    return f"m:truncated={truncated_state} next=use_offset offset={next_offset} limit={limit}"
