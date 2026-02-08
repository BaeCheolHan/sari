import json
import logging
import os
import threading
import time
import inspect
from pathlib import Path
from typing import Any, Dict, Optional

from sari.core.settings import settings
from sari.core.workspace import WorkspaceManager

_TRACE_ENV = "SARI_MCP_TRACE"
_TRACE_PATH_ENV = "SARI_MCP_TRACE_PATH"

_LOGGER: Optional[logging.Logger] = None


def _trace_enabled() -> bool:
    val = (os.environ.get(_TRACE_ENV) or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if (os.environ.get("SARI_MCP_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(settings.DEBUG)


def _resolve_trace_path() -> Path:
    override = (os.environ.get(_TRACE_PATH_ENV) or "").strip()
    if override:
        return Path(os.path.expanduser(override)).resolve()
    log_dir = WorkspaceManager.get_global_log_dir()
    return Path(log_dir) / "mcp_trace.log"


def _get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("sari.mcp.trace")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        try:
            path = _resolve_trace_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path)
        except Exception:
            handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _LOGGER = logger
    return logger


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 2:
        return "[truncated]"
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if len(out) >= 40:
                out["..."] = "[truncated]"
                break
            out[str(k)] = _safe_value(v, depth + 1)
        return out
    if isinstance(value, list):
        return [_safe_value(v, depth + 1) for v in value[:40]]
    if isinstance(value, bytes):
        return "[bytes len=%d]" % len(value)
    if isinstance(value, str):
        if len(value) > 500:
            return value[:300] + "...[truncated]"
        return value
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def trace(event: str, **fields: Any) -> None:
    if not _trace_enabled():
        return
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    loc = None
    if caller is not None:
        code = caller.f_code
        loc = {
            "file": code.co_filename,
            "line": caller.f_lineno,
            "func": code.co_name,
        }
    payload: Dict[str, Any] = {
        "ts": time.time(),
        "event": event,
        "pid": os.getpid(),
        "tid": threading.get_ident(),
    }
    if loc:
        payload["loc"] = loc
    if fields:
        payload["fields"] = _safe_value(fields)

    logger = _get_logger()
    try:
        logger.info(json.dumps(payload, ensure_ascii=True))
    except Exception:
        logger.info(str(payload))
