"""Subinterpreter executor 유틸리티."""

from __future__ import annotations

import concurrent.futures
from typing import Any


def normalize_executor_mode(raw: str, *, default: str = "inline") -> str:
    """executor 모드를 안전하게 정규화한다."""
    value = str(raw or "").strip().lower()
    if value in {"inline", "subinterp"}:
        return value
    return default


def parse_positive_int(raw: object, *, default: int) -> int:
    """양수 정수 설정을 파싱한다."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def parse_non_negative_int(raw: object, *, default: int) -> int:
    """0 이상 정수 설정을 파싱한다."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def create_interpreter_pool_executor(max_workers: int) -> concurrent.futures.Executor | None:
    """가능한 경우 InterpreterPoolExecutor를 생성한다."""
    executor_cls: Any = getattr(concurrent.futures, "InterpreterPoolExecutor", None)
    if executor_cls is None:
        return None
    try:
        return executor_cls(max_workers=max(1, int(max_workers)))
    except (RuntimeError, OSError, TypeError, ValueError):
        return None
