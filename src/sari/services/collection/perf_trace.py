"""성능 병목 분석용 경량 트레이서."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import threading
import time
from functools import wraps
from itertools import count
from typing import Any, Callable, TypeVar

_LOGGER = logging.getLogger("sari.perf.trace")
_CALL_SEQ = count(1)
_TRACE_SUMMARY_LOCK = threading.RLock()
_TRACE_SUMMARY: dict[str, dict[tuple[str, str, str, str, str], dict[str, Any]]] = {}
_TRACE_SESSION_LOCAL = threading.local()
_TRACE_PROCESS_SESSION_LOCK = threading.RLock()
_TRACE_PROCESS_SESSION_ID: str | None = None
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
        self._enabled = _parse_bool_env("SARI_PERF_TRACE", self._enabled)
        return self._enabled

    def should_sample(self) -> bool:
        self._sample_every = _parse_int_env("SARI_PERF_TRACE_EVERY", self._sample_every, 1)
        self._sequence += 1
        return self._sequence % self._sample_every == 0

    def emit(self, event: str, **fields: object) -> None:
        if not self._enabled:
            self._enabled = _parse_bool_env("SARI_PERF_TRACE", self._enabled)
        if not self._enabled:
            return
        payload: dict[str, object] = {
            "component": self._component,
            "event": event,
            "ts_ms": int(time.time() * 1000),
        }
        session_id = current_perf_trace_session_id()
        if session_id is not None:
            payload["session_id"] = session_id
        payload["thread"] = threading.current_thread().name
        payload.update(fields)
        _LOGGER.info("sari_perf_trace %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if event == "span":
            elapsed_ms = fields.get("elapsed_ms")
            if isinstance(elapsed_ms, (int, float)):
                _record_trace_summary(
                    session_id=session_id,
                    component=self._component,
                    event=str(fields.get("name", "unknown")),
                    elapsed_ms=float(elapsed_ms),
                    language=_safe_dim(fields.get("language")),
                    phase=_safe_dim(fields.get("phase")),
                    request_kind=_safe_dim(fields.get("request_kind")),
                )

    @contextmanager
    def span(self, name: str, **fields: object):
        """구간 실행 시간을 단일 span 이벤트로 기록한다."""
        if not self.enabled:
            yield
            return
        started_at = time.perf_counter()
        error_type: str | None = None
        try:
            yield
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            payload = dict(fields)
            payload["name"] = name
            payload["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000.0, 3)
            if error_type is not None:
                payload["error_type"] = error_type
            self.emit("span", **payload)


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


def _safe_dim(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def current_perf_trace_session_id() -> str | None:
    local_value = getattr(_TRACE_SESSION_LOCAL, "session_id", None)
    if isinstance(local_value, str) and local_value.strip() != "":
        return local_value
    with _TRACE_PROCESS_SESSION_LOCK:
        if isinstance(_TRACE_PROCESS_SESSION_ID, str) and _TRACE_PROCESS_SESSION_ID.strip() != "":
            return _TRACE_PROCESS_SESSION_ID
    explicit = os.getenv("SARI_PERF_TRACE_SESSION_ID", "").strip()
    if explicit != "":
        return explicit
    return None


@contextmanager
def perf_trace_session(session_id: str):
    """현재 스레드에서 사용할 perf trace session id를 임시 설정한다."""
    global _TRACE_PROCESS_SESSION_ID
    previous = getattr(_TRACE_SESSION_LOCAL, "session_id", None)
    with _TRACE_PROCESS_SESSION_LOCK:
        previous_process = _TRACE_PROCESS_SESSION_ID
        _TRACE_PROCESS_SESSION_ID = session_id
    _TRACE_SESSION_LOCAL.session_id = session_id
    try:
        yield
    finally:
        with _TRACE_PROCESS_SESSION_LOCK:
            _TRACE_PROCESS_SESSION_ID = previous_process
        if previous is None:
            if hasattr(_TRACE_SESSION_LOCAL, "session_id"):
                delattr(_TRACE_SESSION_LOCAL, "session_id")
        else:
            _TRACE_SESSION_LOCAL.session_id = previous


def reset_perf_trace_summary(session_id: str) -> None:
    with _TRACE_SUMMARY_LOCK:
        _TRACE_SUMMARY.pop(session_id, None)


def get_perf_trace_summary(session_id: str, top_n: int = 20) -> dict[str, object]:
    with _TRACE_SUMMARY_LOCK:
        raw = dict(_TRACE_SUMMARY.get(session_id, {}))
    items: list[dict[str, object]] = []
    for (component, event, language, phase, request_kind), stats in raw.items():
        samples = list(stats.get("samples_ms", []))
        samples.sort()
        count = int(stats.get("count", 0))
        total_ms = float(stats.get("total_ms", 0.0))
        max_ms = float(stats.get("max_ms", 0.0))
        p50 = _percentile(samples, 50.0)
        p95 = _percentile(samples, 95.0)
        p99 = _percentile(samples, 99.0)
        item = {
            "component": component,
            "event": event,
            "language": language,
            "phase": phase,
            "request_kind": request_kind,
            "count": count,
            "total_ms": round(total_ms, 3),
            "avg_ms": round(total_ms / count, 3) if count > 0 else 0.0,
            "max_ms": round(max_ms, 3),
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
            "p99_ms": round(p99, 3),
        }
        items.append(item)
    items.sort(key=lambda x: float(x["total_ms"]), reverse=True)
    return {
        "session_id": session_id,
        "span_groups": items[: max(1, int(top_n))],
        "group_count": len(items),
    }


def _record_trace_summary(
    *,
    session_id: str | None,
    component: str,
    event: str,
    elapsed_ms: float,
    language: str,
    phase: str,
    request_kind: str,
) -> None:
    if session_id is None or session_id == "":
        return
    key = (component, event, language, phase, request_kind)
    with _TRACE_SUMMARY_LOCK:
        session_map = _TRACE_SUMMARY.setdefault(session_id, {})
        stats = session_map.get(key)
        if stats is None:
            stats = {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "samples_ms": []}
            session_map[key] = stats
        stats["count"] = int(stats["count"]) + 1
        stats["total_ms"] = float(stats["total_ms"]) + float(elapsed_ms)
        stats["max_ms"] = max(float(stats["max_ms"]), float(elapsed_ms))
        samples: list[float] = stats["samples_ms"]
        if len(samples) < 20000:
            samples.append(float(elapsed_ms))


def _percentile(samples: list[float], p: float) -> float:
    if len(samples) == 0:
        return 0.0
    if len(samples) == 1:
        return float(samples[0])
    rank = (p / 100.0) * (len(samples) - 1)
    lower = int(rank)
    upper = min(len(samples) - 1, lower + 1)
    if lower == upper:
        return float(samples[lower])
    weight = rank - lower
    return float(samples[lower]) * (1.0 - weight) + float(samples[upper]) * weight
