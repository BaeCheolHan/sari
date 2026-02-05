from typing import Any, Dict, Optional, List, Callable
import time
import threading

import logging
from sari.core.settings import settings


class HttpMiddleware:
    def before(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None

    def after(self, ctx: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
        return response

    def on_error(self, ctx: Dict[str, Any], error: Dict[str, Any]) -> Dict[str, Any]:
        return error


def run_http_middlewares(
    ctx: Dict[str, Any],
    middlewares: List[HttpMiddleware],
    execute_fn: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    for m in middlewares:
        res = m.before(ctx)
        if res:
            return res
    try:
        result = execute_fn()
    except Exception as e:
        err = {"ok": False, "error": str(e)}
        for m in reversed(middlewares):
            err = m.on_error(ctx, err)
        return err
    for m in reversed(middlewares):
        result = m.after(ctx, result)
    return result


class LoggingMiddleware(HttpMiddleware):
    def before(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ctx["__start_ts"] = time.time()
        return None

    def after(self, ctx: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
        if not settings.get_bool("HTTP_LOG_ENABLED", True):
            return response
        if ctx.get("path") == "/health":
            return response
        start = ctx.get("__start_ts")
        if start is not None:
            elapsed_ms = int((time.time() - start) * 1000)
            ctx["__elapsed_ms"] = elapsed_ms
            status = response.get("status", 200) if isinstance(response, dict) else 200
            logging.getLogger("sari.http").info(
                "%s %s %s %sms",
                ctx.get("method", "GET"),
                ctx.get("path", ""),
                status,
                elapsed_ms,
            )
        return response


class RateLimitMiddleware(HttpMiddleware):
    def __init__(self, limit_per_sec: int = 50, burst: int = 100):
        self.limit_per_sec = max(1, limit_per_sec)
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._last = time.time()
        self._lock = threading.Lock()

    def before(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            now = time.time()
            elapsed = max(0.0, now - self._last)
            self._tokens = min(self.burst, self._tokens + elapsed * self.limit_per_sec)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return None
        return {"ok": False, "error": "rate limit", "status": 429}


def default_http_middlewares() -> List[HttpMiddleware]:
    limit = settings.get_int("HTTP_RATE_LIMIT", 50)
    burst = settings.get_int("HTTP_RATE_BURST", 100)
    return [LoggingMiddleware(), RateLimitMiddleware(limit_per_sec=limit, burst=burst)]
