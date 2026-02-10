import json
import os
import urllib.parse
import logging
from enum import Enum
from typing import Any, Dict, Optional, List, Callable

logger = logging.getLogger("sari.mcp.protocol")


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

# --- Encoding Helpers ---


def pack_encode_text(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="")


def pack_encode_id(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="/._-:@")

# --- Header & Line Packing ---


def pack_header(tool: str,
                kv: Dict[str,
                         Any],
                returned: Optional[int] = None,
                total: Optional[int] = None,
                total_mode: Optional[str] = None) -> str:
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


def pack_line(kind: str,
              kv: Optional[Dict[str,
                                str]] = None,
              single_value: Optional[str] = None) -> str:
    if single_value is not None:
        return f"{kind}:{single_value}"
    if kv:
        field_strs = [f"{k}={v}" for k, v in kv.items()]
        return f"{kind}:{ ' '.join(field_strs) }"
    return f"{kind}:"


def pack_error(tool: str,
               code: Any,
               msg: str,
               hints: List[str] = None,
               trace: str = None,
               fields: Dict[str,
                            Any] = None) -> str:
    parts = [
        "PACK1", f"tool={tool}", "ok=false",
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

# --- Response Orchestration ---


def _get_env_any(key: str, default: str = "") -> str:
    for p in ["SARI_", "CODEX_", "GEMINI_", ""]:
        val = os.environ.get(p + key)
        if val is not None:
            return val
    return default


def _get_format() -> str:
    fmt = _get_env_any("FORMAT", "pack").lower()
    return "pack" if fmt == "pack" else "json"


def _compact_enabled() -> bool:
    return _get_env_any(
        "RESPONSE_COMPACT",
        "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on")


def mcp_response(
    tool_name: str,
    pack_func: Callable[[], str],
    json_func: Callable[[], Dict[str, Any]]
) -> Dict[str, Any]:
    fmt = _get_format()
    try:
        if fmt == "pack":
            text = pack_func()
            out = {"content": [{"type": "text", "text": text}]}
            first_line = str(text).splitlines()[0] if str(text) else ""
            if first_line.startswith("PACK1 ") and " ok=false" in first_line:
                out["isError"] = True
            return out
        else:
            data = json_func()
            compact = _compact_enabled()
            json_text = json.dumps(data, ensure_ascii=False,
                                   separators=(",", ":") if compact else None,
                                   indent=None if compact else 2)
            res = {"content": [{"type": "text", "text": json_text}]}
            if isinstance(data, dict):
                for k, v in data.items():
                    if k not in res:
                        res[k] = v
            return res
    except Exception as e:
        import traceback
        err_msg = f"Internal Error in {tool_name}: {str(e)}"
        stack = traceback.format_exc()
        logger.error(err_msg, exc_info=True)
        if fmt == "pack":
            return {"content": [{"type": "text", "text": pack_error(
                tool_name, ErrorCode.INTERNAL, err_msg, trace=stack)}], "isError": True}
        else:
            return {"content": [{"type": "text",
                                 "text": json.dumps({"error": err_msg,
                                                     "trace": stack})}],
                    "isError": True,
                    "error": {"code": ErrorCode.INTERNAL.value,
                              "message": err_msg}}
