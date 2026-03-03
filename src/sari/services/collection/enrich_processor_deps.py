"""Enrich/L2 processor 구성 파라미터 묶음."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class EnrichProcessorDeps:
    """L2/Enrich processor 공통 의존성을 한 번에 전달하기 위한 DTO."""

    assert_parent_alive: Callable[[str], None]
    rebalance_jobs_by_language: Callable[[list], list]
    file_repo_get_file: Callable
    retry_max_attempts: int
    retry_backoff_base_sec: float
    persist_body_for_read: bool
    vector_index_sink: object | None
    is_deletion_hold_enabled: Callable[[], bool]
    resolve_l3_skip_reason: Callable
    build_l3_skipped_readiness: Callable
    record_error_event: Callable
    record_enrich_latency: Callable[[float], None]
    run_mode: str
    record_event: Callable | None
