from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Mapping

from sari.mcp.stabilization.analytics_queue import enqueue_analytics
from sari.mcp.stabilization.session_keys import resolve_session_key, strict_session_id_enabled

@dataclass
class _SessionMetrics:
    reads_count: int = 0
    reads_lines_total: int = 0
    reads_chars_total: int = 0
    search_count: int = 0
    read_span_sum: int = 0
    max_read_span: int = 0
    preview_degraded_count: int = 0
    reads_after_search_count: int = 0
    last_search_query: str = ""
    last_search_top_paths: tuple[str, ...] = ()
    last_search_candidates: dict[str, str] | None = None
    last_bundle_id: str = ""
    last_seen_seq: int = 0


_LOCK = threading.RLock()
_SESSION_METRICS: dict[str, _SessionMetrics] = {}
_SEQUENCE = 0


def _next_sequence() -> int:
    global _SEQUENCE
    _SEQUENCE += 1
    return _SEQUENCE


def _session_key(args: Mapping[str, object] | object, roots: list[str]) -> str:
    return resolve_session_key(args, roots)


def _get_state(session_key: str) -> _SessionMetrics:
    state = _SESSION_METRICS.get(session_key)
    if state is None:
        state = _SessionMetrics()
        _SESSION_METRICS[session_key] = state
    return state


def _enqueue_analytics_snapshot(event_type: str, session_key: str, state: _SessionMetrics) -> None:
    enqueue_analytics(
        {
            "event_type": event_type,
            "session_key": session_key,
            "snapshot": _snapshot(state),
            "seq": state.last_seen_seq,
        }
    )


def _snapshot(state: _SessionMetrics) -> dict[str, float | int]:
    ratio = (
        state.reads_after_search_count / state.reads_count
        if state.reads_count > 0
        else 0.0
    )
    avg_span = (
        state.read_span_sum / state.reads_count
        if state.reads_count > 0
        else 0.0
    )
    return {
        "reads_count": state.reads_count,
        "reads_lines_total": state.reads_lines_total,
        "reads_chars_total": state.reads_chars_total,
        "search_count": state.search_count,
        "read_after_search_ratio": round(ratio, 6),
        "avg_read_span": round(avg_span, 6),
        "max_read_span": state.max_read_span,
        "preview_degraded_count": state.preview_degraded_count,
    }


def record_search_metrics(
    args: Mapping[str, object] | object,
    roots: list[str],
    *,
    preview_degraded: bool,
    query: str = "",
    top_paths: list[str] | None = None,
    candidates: Mapping[str, str] | None = None,
    bundle_id: str = "",
    db: object = None,
) -> dict[str, float | int]:
    key = _session_key(args, roots)
    with _LOCK:
        state = _get_state(key)
        state.search_count += 1
        state.last_seen_seq = _next_sequence()
        state.last_search_query = str(query or "").strip()
        if top_paths:
            state.last_search_top_paths = tuple(str(p) for p in top_paths if str(p).strip())
        if candidates:
            state.last_search_candidates = {str(k): str(v) for k, v in candidates.items()}
        if bundle_id:
            state.last_bundle_id = str(bundle_id)
        if preview_degraded:
            state.preview_degraded_count += 1
        _enqueue_analytics_snapshot("search", key, state)
        return _snapshot(state)


def record_read_metrics(
    args: Mapping[str, object] | object,
    roots: list[str],
    *,
    read_lines: int,
    read_chars: int,
    read_span: int,
    db: object = None,
) -> dict[str, float | int]:
    key = _session_key(args, roots)
    with _LOCK:
        state = _get_state(key)
        state.reads_count += 1
        state.last_seen_seq = _next_sequence()
        state.reads_lines_total += max(0, int(read_lines))
        state.reads_chars_total += max(0, int(read_chars))
        span = max(0, int(read_span))
        state.read_span_sum += span
        state.max_read_span = max(state.max_read_span, span)
        if state.search_count > 0:
            state.reads_after_search_count += 1
        _enqueue_analytics_snapshot("read", key, state)
        return _snapshot(state)


def get_metrics_snapshot(
    args: Mapping[str, object] | object,
    roots: list[str],
) -> dict[str, float | int]:
    key = _session_key(args, roots)
    with _LOCK:
        return _snapshot(_get_state(key))


def get_session_key(
    args: Mapping[str, object] | object,
    roots: list[str],
) -> str:
    return _session_key(args, roots)


def get_search_context(
    args: Mapping[str, object] | object,
    roots: list[str],
) -> dict[str, object]:
    key = _session_key(args, roots)
    with _LOCK:
        state = _get_state(key)
        return {
            "last_search_query": state.last_search_query,
            "last_search_top_paths": list(state.last_search_top_paths),
            "last_search_candidates": dict(state.last_search_candidates or {}),
            "last_bundle_id": state.last_bundle_id,
            "search_count": state.search_count,
        }


def requires_strict_session_id(
    args: Mapping[str, object] | object,
) -> bool:
    if not strict_session_id_enabled():
        return False
    args_map = args if isinstance(args, Mapping) else {}
    return not str(args_map.get("session_id") or "").strip()


def reset_session_metrics_for_tests() -> None:
    global _SEQUENCE
    with _LOCK:
        _SESSION_METRICS.clear()
        _SEQUENCE = 0
