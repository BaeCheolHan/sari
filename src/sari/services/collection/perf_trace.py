"""성능 병목 분석용 경량 트레이서."""

from __future__ import annotations

import json
import logging
import os
import time
from functools import wraps
from itertools import count
from typing import Any, Callable, TypeVar

_LOGGER = logging.getLogger("sari.perf.trace")
_CALL_SEQ = count(1)
F = TypeVar("F", bound=Callable[..., Any])


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, value)


class PerfTracer:
    """환경변수로 on/off 가능한 구조화 로그 트레이서."""

    def __init__(self, component: str) -> None:
        self._component = component
        self._enabled = _parse_bool_env("SARI_PERF_TRACE", False)
        self._sample_every = _parse_int_env("SARI_PERF_TRACE_EVERY", 1, 1)
        self._sequence = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_sample(self) -> bool:
        self._sequence += 1
        return self._sequence % self._sample_every == 0

    def emit(self, event: str, **fields: object) -> None:
        if not self._enabled:
            return
        payload: dict[str, object] = {
            "component": self._component,
            "event": event,
            "ts_ms": int(time.time() * 1000),
        }
        payload.update(fields)
        _LOGGER.info("sari_perf_trace %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def trace_methods(component: str) -> Callable[[type], type]:
    """클래스의 (magic 제외) 모든 메서드 start/end/error를 자동 추적한다."""

    def _decorate(cls: type) -> type:
        for name, value in list(cls.__dict__.items()):
            if name.startswith("__") and name.endswith("__"):
                continue
            if isinstance(value, staticmethod):
                func = value.__func__
                wrapped = staticmethod(_wrap_callable(component=component, method=name, fn=func))
                setattr(cls, name, wrapped)
                continue
            if isinstance(value, classmethod):
                func = value.__func__
                wrapped = classmethod(_wrap_callable(component=component, method=name, fn=func))
                setattr(cls, name, wrapped)
                continue
            if callable(value):
                setattr(cls, name, _wrap_callable(component=component, method=name, fn=value))
        return cls

    return _decorate


def trace_function(component: str, name: str | None = None) -> Callable[[F], F]:
    """단일 함수 start/end/error를 추적한다."""

    def _decorate(fn: F) -> F:
        method_name = fn.__name__ if name is None else name
        return _wrap_callable(component=component, method=method_name, fn=fn)

    return _decorate


def _wrap_callable(component: str, method: str, fn: F) -> F:
    @wraps(fn)
    def _wrapped(*args: object, **kwargs: object):  # type: ignore[misc]
        tracer = PerfTracer(component=component)
        if not tracer.enabled:
            return fn(*args, **kwargs)
        call_id = next(_CALL_SEQ)
        started_at = time.perf_counter()
        tracer.emit("fn_start", call_id=call_id, method=method)
        try:
            result = fn(*args, **kwargs)
            tracer.emit(
                "fn_end",
                call_id=call_id,
                method=method,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
            )
            return result
        except Exception as exc:
            tracer.emit(
                "fn_error",
                call_id=call_id,
                method=method,
                error_type=type(exc).__name__,
                error_message=str(exc),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
            )
            raise

    return _wrapped  # type: ignore[return-value]
