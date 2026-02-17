"""세션 단위 stabilization 메트릭 상태를 관리한다."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Mapping

from sari.mcp.stabilization.analytics_queue import enqueue_analytics
from sari.mcp.stabilization.session_keys import resolve_session_key, strict_session_id_enabled


@dataclass
class _SessionMetrics:
    """세션 메트릭 누적 상태를 표현한다."""

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
_MAX_SESSION_METRICS = max(64, int(os.environ.get("SARI_SESSION_METRICS_MAX", "4096") or "4096"))


def _next_sequence() -> int:
    """증분 시퀀스를 반환한다."""
    global _SEQUENCE
    _SEQUENCE += 1
    return _SEQUENCE


def _session_key(args: Mapping[str, object] | object, roots: list[str]) -> str:
    """요청별 세션 키를 계산한다."""
    return resolve_session_key(args, roots)


def _evict_metrics_if_needed() -> None:
    """메트릭 캐시 상한을 넘으면 오래된 항목부터 제거한다."""
    if len(_SESSION_METRICS) < _MAX_SESSION_METRICS:
        return
    overflow = len(_SESSION_METRICS) - _MAX_SESSION_METRICS + 1
    victims = sorted(_SESSION_METRICS.items(), key=lambda item: int(item[1].last_seen_seq or 0))[:overflow]
    for key, _state in victims:
        _SESSION_METRICS.pop(key, None)


def _state_of(session_key: str) -> _SessionMetrics:
    """세션 키에 대응하는 메트릭 상태를 반환한다."""
    state = _SESSION_METRICS.get(session_key)
    if state is None:
        _evict_metrics_if_needed()
        state = _SessionMetrics()
        _SESSION_METRICS[session_key] = state
    return state


def _snapshot(state: _SessionMetrics) -> dict[str, float | int]:
    """메트릭 상태를 외부 응답용 스냅샷으로 변환한다."""
    ratio = (state.reads_after_search_count / state.reads_count) if state.reads_count > 0 else 0.0
    avg_span = (state.read_span_sum / state.reads_count) if state.reads_count > 0 else 0.0
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


def _enqueue_analytics_snapshot(event_type: str, session_key: str, state: _SessionMetrics) -> None:
    """분석 큐에 스냅샷 이벤트를 넣는다."""
    enqueue_analytics(
        {
            "event_type": event_type,
            "session_key": session_key,
            "snapshot": _snapshot(state),
            "seq": state.last_seen_seq,
        }
    )


def record_search_metrics(
    args: Mapping[str, object] | object,
    roots: list[str],
    preview_degraded: bool,
    query: str = "",
    top_paths: list[str] | None = None,
    candidates: Mapping[str, str] | None = None,
    bundle_id: str = "",
) -> dict[str, float | int]:
    """search 메트릭을 누적하고 최신 스냅샷을 반환한다."""
    key = _session_key(args, roots)
    with _LOCK:
        state = _state_of(key)
        state.search_count += 1
        state.last_seen_seq = _next_sequence()
        state.last_search_query = str(query or "").strip()
        if top_paths is not None:
            state.last_search_top_paths = tuple(str(path) for path in top_paths if str(path).strip() != "")
        if candidates is not None:
            state.last_search_candidates = {str(k): str(v) for k, v in candidates.items()}
        if bundle_id != "":
            state.last_bundle_id = str(bundle_id)
        if preview_degraded:
            state.preview_degraded_count += 1
        _enqueue_analytics_snapshot("search", key, state)
        return _snapshot(state)


def record_read_metrics(
    args: Mapping[str, object] | object,
    roots: list[str],
    read_lines: int,
    read_chars: int,
    read_span: int,
) -> dict[str, float | int]:
    """read 메트릭을 누적하고 최신 스냅샷을 반환한다."""
    key = _session_key(args, roots)
    with _LOCK:
        state = _state_of(key)
        state.reads_count += 1
        state.last_seen_seq = _next_sequence()
        safe_lines = max(0, int(read_lines))
        safe_chars = max(0, int(read_chars))
        safe_span = max(0, int(read_span))
        state.reads_lines_total += safe_lines
        state.reads_chars_total += safe_chars
        state.read_span_sum += safe_span
        state.max_read_span = max(state.max_read_span, safe_span)
        if state.search_count > 0:
            state.reads_after_search_count += 1
        _enqueue_analytics_snapshot("read", key, state)
        return _snapshot(state)


def get_metrics_snapshot(args: Mapping[str, object] | object, roots: list[str]) -> dict[str, float | int]:
    """세션 메트릭 스냅샷을 반환한다."""
    key = _session_key(args, roots)
    with _LOCK:
        return _snapshot(_state_of(key))


def get_session_key(args: Mapping[str, object] | object, roots: list[str]) -> str:
    """요청 인자에서 계산된 세션 키를 반환한다."""
    return _session_key(args, roots)


def get_search_context(args: Mapping[str, object] | object, roots: list[str]) -> dict[str, object]:
    """최근 search 컨텍스트를 반환한다."""
    key = _session_key(args, roots)
    with _LOCK:
        state = _state_of(key)
        return {
            "last_search_query": state.last_search_query,
            "last_search_top_paths": list(state.last_search_top_paths),
            "last_search_candidates": dict(state.last_search_candidates or {}),
            "last_bundle_id": state.last_bundle_id,
            "search_count": state.search_count,
        }


def requires_strict_session_id(args: Mapping[str, object] | object) -> bool:
    """strict session 정책에서 session_id 누락 여부를 반환한다."""
    if not strict_session_id_enabled():
        return False
    args_map = args if isinstance(args, Mapping) else {}
    session_id = str(args_map.get("session_id") or "").strip()
    return session_id == ""


def reset_session_metrics_for_tests() -> None:
    """테스트를 위해 세션 메트릭 상태를 초기화한다."""
    global _SEQUENCE
    with _LOCK:
        _SESSION_METRICS.clear()
        _SEQUENCE = 0

