from typing import Callable, Optional, TypeAlias
import time
import threading

import logging
from sari.core.settings import settings

HttpContext: TypeAlias = dict[str, object]
HttpResult: TypeAlias = dict[str, object]


class HttpMiddleware:
    def before(self, ctx: HttpContext) -> Optional[HttpResult]:
        return None

    def after(self, ctx: HttpContext, response: HttpResult) -> HttpResult:
        return response

    def on_error(self, ctx: HttpContext, error: HttpResult) -> HttpResult:
        return error


def _sanitize_error_message(exc: object, fallback: str = "request failed") -> str:
    raw = str(exc).strip() if exc is not None else ""
    if not raw:
        return fallback
    text = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    return text[:500]


def run_http_middlewares(
    ctx: HttpContext,
    middlewares: list[HttpMiddleware],
    execute_fn: Callable[[], object],
) -> HttpResult:
    for m in middlewares:
        res = m.before(ctx)
        if res:
            return res
    try:
        result = execute_fn()
        if not isinstance(result, dict):
            raise TypeError("http execute_fn must return an object")
    except Exception as e:
        err = {
            "ok": False,
            "error": _sanitize_error_message(e),
            "reason_code": "HTTP_MIDDLEWARE_EXEC_FAILED",
        }
        for m in reversed(middlewares):
            err = m.on_error(ctx, err)
        return err
    for m in reversed(middlewares):
        result = m.after(ctx, result)
    return result


class LoggingMiddleware(HttpMiddleware):
    def before(self, ctx: HttpContext) -> Optional[HttpResult]:
        ctx["__start_ts"] = time.time()
        return None

    def after(self, ctx: HttpContext, response: HttpResult) -> HttpResult:
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

    def before(self, ctx: HttpContext) -> Optional[HttpResult]:
        with self._lock:
            now = time.time()
            elapsed = max(0.0, now - self._last)
            self._tokens = min(self.burst, self._tokens + elapsed * self.limit_per_sec)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return None
        return {"ok": False, "error": "rate limit", "status": 429}


def default_http_middlewares() -> list[HttpMiddleware]:
    limit = settings.get_int("HTTP_RATE_LIMIT", 50)
    burst = settings.get_int("HTTP_RATE_BURST", 100)
    return [LoggingMiddleware(), RateLimitMiddleware(limit_per_sec=limit, burst=burst)]
