from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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


_LOCK = threading.RLock()
_SESSION_METRICS: dict[str, _SessionMetrics] = {}


def _normalize_session_id(value: object) -> str:
    text = str(value or "").strip()
    return text


def _session_key(args: Mapping[str, object] | object, roots: list[str]) -> str:
    args_map = args if isinstance(args, Mapping) else {}
    explicit = _normalize_session_id(args_map.get("session_id")) if isinstance(args_map, Mapping) else ""
    if explicit:
        return explicit

    for env_key in ("SARI_SESSION_ID", "CODEX_SESSION_ID"):
        env_val = _normalize_session_id(os.environ.get(env_key))
        if env_val:
            return env_val

    normalized_roots = [str(Path(r).expanduser()) for r in roots if str(r or "").strip()]
    if normalized_roots:
        return "|".join(sorted(normalized_roots))
    return "global"


def _get_state(session_key: str) -> _SessionMetrics:
    state = _SESSION_METRICS.get(session_key)
    if state is None:
        state = _SessionMetrics()
        _SESSION_METRICS[session_key] = state
    return state


def _maybe_persist_placeholder(_db: object, _session_key: str, _state: _SessionMetrics) -> None:
    """sqlite opt-in placeholder (no-op for Task 3)."""
    backend = str(os.environ.get("SARI_STABILIZATION_METRICS_BACKEND", "")).strip().lower()
    if backend == "sqlite":
        return


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
    db: object = None,
) -> dict[str, float | int]:
    key = _session_key(args, roots)
    with _LOCK:
        state = _get_state(key)
        state.search_count += 1
        state.last_search_query = str(query or "").strip()
        if top_paths:
            state.last_search_top_paths = tuple(str(p) for p in top_paths if str(p).strip())
        if preview_degraded:
            state.preview_degraded_count += 1
        _maybe_persist_placeholder(db, key, state)
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
        state.reads_lines_total += max(0, int(read_lines))
        state.reads_chars_total += max(0, int(read_chars))
        span = max(0, int(read_span))
        state.read_span_sum += span
        state.max_read_span = max(state.max_read_span, span)
        if state.search_count > 0:
            state.reads_after_search_count += 1
        _maybe_persist_placeholder(db, key, state)
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
            "search_count": state.search_count,
        }


def reset_session_metrics_for_tests() -> None:
    with _LOCK:
        _SESSION_METRICS.clear()
