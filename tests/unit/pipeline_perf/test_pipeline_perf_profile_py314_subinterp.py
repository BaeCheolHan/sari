from __future__ import annotations

from sari.services.pipeline.perf_service import PipelinePerfService


def test_resolve_threshold_profile_supports_py314_subinterp_workspace_real() -> None:
    service = PipelinePerfService.__new__(PipelinePerfService)
    profile = service._resolve_threshold_profile(  # noqa: SLF001
        run_context={"config_snapshot": {"profile": "py314_subinterp_v1"}},
        dataset_type="workspace_real",
    )
    assert profile["profile_name"] == "py314_subinterp_v1"
    assert profile["min_l3_jobs_per_sec"] == 70.0
    assert profile["max_wall_time_sec"] == 55.0
    assert profile["max_error_rate_pct"] == 0.5


def test_resolve_threshold_profile_supports_py314_subinterp_non_workspace_real() -> None:
    service = PipelinePerfService.__new__(PipelinePerfService)
    profile = service._resolve_threshold_profile(  # noqa: SLF001
        run_context={"config_snapshot": {"profile": "py314_subinterp_v1"}},
        dataset_type="sample_2k",
    )
    assert profile["profile_name"] == "py314_subinterp_v1"
    assert profile["min_l3_jobs_per_sec"] == 300.0
    assert profile["max_wall_time_sec"] == 13.0
    assert profile["max_error_rate_pct"] == 0.5
