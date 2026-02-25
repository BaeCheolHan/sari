from __future__ import annotations

from sari.services.pipeline.ab_report import compare_case_metrics, extract_workspace_metrics


def _summary(
    *,
    wall_time_sec: float,
    l3_jobs_per_sec: float,
    l5_total_pct: float,
    l5_batch_pct: float,
    p95_pending_age: float | None,
) -> dict[str, object]:
    return {
        "datasets": [
            {
                "dataset_type": "workspace_real",
                "wall_time_sec": wall_time_sec,
                "l3_jobs_per_sec": l3_jobs_per_sec,
                "integrity": {
                    "pending_age_stats": {
                        "p95_pending_available_age_sec": p95_pending_age,
                    },
                    "lsp_runtime_metrics": {
                        "l5_call_rate_total_pct": l5_total_pct,
                        "l5_call_rate_batch_pct": l5_batch_pct,
                        "l5_reject_count_by_reject_reason_pressure_rate_exceeded": 2.0,
                    },
                },
            }
        ]
    }


def test_extract_workspace_metrics_reads_runtime_and_integrity_fields() -> None:
    metrics = extract_workspace_metrics(
        _summary(
            wall_time_sec=12.5,
            l3_jobs_per_sec=88.2,
            l5_total_pct=4.0,
            l5_batch_pct=0.5,
            p95_pending_age=9.1,
        )
    )
    assert metrics.wall_time_sec == 12.5
    assert metrics.l3_jobs_per_sec == 88.2
    assert metrics.l5_call_rate_total_pct == 4.0
    assert metrics.l5_call_rate_batch_pct == 0.5
    assert metrics.p95_pending_available_age_sec == 9.1
    assert metrics.l5_reject_counts["pressure_rate_exceeded"] == 2.0


def test_compare_case_metrics_computes_deltas_from_mean() -> None:
    a = [
        extract_workspace_metrics(_summary(wall_time_sec=10.0, l3_jobs_per_sec=100.0, l5_total_pct=4.0, l5_batch_pct=0.4, p95_pending_age=8.0)),
        extract_workspace_metrics(_summary(wall_time_sec=12.0, l3_jobs_per_sec=90.0, l5_total_pct=5.0, l5_batch_pct=0.6, p95_pending_age=10.0)),
    ]
    b = [
        extract_workspace_metrics(_summary(wall_time_sec=9.0, l3_jobs_per_sec=110.0, l5_total_pct=3.0, l5_batch_pct=0.3, p95_pending_age=7.0)),
        extract_workspace_metrics(_summary(wall_time_sec=11.0, l3_jobs_per_sec=95.0, l5_total_pct=4.0, l5_batch_pct=0.5, p95_pending_age=9.0)),
    ]

    cmp = compare_case_metrics(case_a=a, case_b=b)
    assert cmp["wall_time_sec"]["a_mean"] == 11.0
    assert cmp["wall_time_sec"]["b_mean"] == 10.0
    assert cmp["wall_time_sec"]["delta_b_minus_a"] == -1.0
    assert cmp["l3_jobs_per_sec"]["delta_b_minus_a"] == 7.5
    assert cmp["l5_call_rate_total_pct"]["delta_b_minus_a"] == -1.0
