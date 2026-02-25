"""A/B pipeline perf summary 비교 유틸리티."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspacePerfMetrics:
    """workspace_real 기준 핵심 지표 스냅샷."""

    wall_time_sec: float | None
    l3_jobs_per_sec: float | None
    p95_pending_available_age_sec: float | None
    l5_call_rate_total_pct: float | None
    l5_call_rate_batch_pct: float | None
    search_latency_ms_p95: float | None
    read_latency_ms_p95: float | None
    l5_reject_counts: dict[str, float]
    l5_cost_units_by_reason: dict[str, float]
    l5_cost_units_by_language: dict[str, float]
    l5_cost_units_by_workspace: dict[str, float]


def extract_workspace_metrics(summary: dict[str, object]) -> WorkspacePerfMetrics:
    """pipeline perf summary에서 workspace_real 지표를 추출한다."""
    datasets = summary.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("invalid summary: datasets missing")
    workspace = None
    for item in datasets:
        if isinstance(item, dict) and str(item.get("dataset_type")) == "workspace_real":
            workspace = item
            break
    if workspace is None:
        raise ValueError("workspace_real dataset missing")

    integrity = workspace.get("integrity")
    if not isinstance(integrity, dict):
        integrity = {}
    runtime = integrity.get("lsp_runtime_metrics")
    if not isinstance(runtime, dict):
        runtime = {}
    pending = integrity.get("pending_age_stats")
    if not isinstance(pending, dict):
        pending = {}

    return WorkspacePerfMetrics(
        wall_time_sec=_to_float_or_none(workspace.get("wall_time_sec")),
        l3_jobs_per_sec=_to_float_or_none(workspace.get("l3_jobs_per_sec")),
        p95_pending_available_age_sec=_to_float_or_none(pending.get("p95_pending_available_age_sec")),
        l5_call_rate_total_pct=_to_float_or_none(runtime.get("l5_call_rate_total_pct")),
        l5_call_rate_batch_pct=_to_float_or_none(runtime.get("l5_call_rate_batch_pct")),
        search_latency_ms_p95=_to_float_or_none(runtime.get("search_latency_ms_p95")),
        read_latency_ms_p95=_to_float_or_none(runtime.get("read_latency_ms_p95")),
        l5_reject_counts=_prefix_values(runtime, "l5_reject_count_by_reject_reason_"),
        l5_cost_units_by_reason=_prefix_values(runtime, "l5_cost_units_total_by_reason_"),
        l5_cost_units_by_language=_prefix_values(runtime, "l5_cost_units_total_by_language_"),
        l5_cost_units_by_workspace=_prefix_values(runtime, "l5_cost_units_total_by_workspace_"),
    )


def compare_case_metrics(
    *,
    case_a: list[WorkspacePerfMetrics],
    case_b: list[WorkspacePerfMetrics],
) -> dict[str, dict[str, float | None]]:
    """A/B 지표 평균과 차이를 계산한다."""
    return {
        "wall_time_sec": _compare_field(case_a, case_b, "wall_time_sec"),
        "l3_jobs_per_sec": _compare_field(case_a, case_b, "l3_jobs_per_sec"),
        "p95_pending_available_age_sec": _compare_field(case_a, case_b, "p95_pending_available_age_sec"),
        "l5_call_rate_total_pct": _compare_field(case_a, case_b, "l5_call_rate_total_pct"),
        "l5_call_rate_batch_pct": _compare_field(case_a, case_b, "l5_call_rate_batch_pct"),
        "search_latency_ms_p95": _compare_field(case_a, case_b, "search_latency_ms_p95"),
        "read_latency_ms_p95": _compare_field(case_a, case_b, "read_latency_ms_p95"),
    }


def _compare_field(
    case_a: list[WorkspacePerfMetrics],
    case_b: list[WorkspacePerfMetrics],
    field_name: str,
) -> dict[str, float | None]:
    a_mean = _mean([getattr(item, field_name) for item in case_a])
    b_mean = _mean([getattr(item, field_name) for item in case_b])
    if a_mean is None or b_mean is None:
        delta = None
    else:
        delta = b_mean - a_mean
    return {
        "a_mean": a_mean,
        "b_mean": b_mean,
        "delta_b_minus_a": delta,
    }


def _mean(values: list[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    if not items:
        return None
    return sum(items) / float(len(items))


def _prefix_values(runtime: dict[str, object], prefix: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in runtime.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        number = _to_float_or_none(value)
        if number is None:
            continue
        out[key[len(prefix) :]] = number
    return out


def _to_float_or_none(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
