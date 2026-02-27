"""성능 회귀 게이트 스크립트를 검증한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module(module_path: Path, module_name: str) -> object:
    """파일 경로 기반으로 모듈을 동적으로 로드한다."""
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module spec load failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _summary(scan_p95: float, enrich_sec: float, search_p95: float, done: int) -> dict[str, object]:
    """테스트용 벤치마크 요약 payload를 생성한다."""
    return {
        "scan": {"ingest_latency_ms_p95": scan_p95},
        "enrich": {"completion_sec": enrich_sec, "done_count": done},
        "search": {"search_latency_ms_p95": search_p95},
    }


def test_perf_regression_check_passes_when_candidate_is_better() -> None:
    """후보 성능이 기준선보다 좋거나 같으면 통과해야 한다."""
    script_path = Path(__file__).resolve().parents[3] / "tools" / "quality" / "perf_regression_check.py"
    module = _load_module(script_path, "perf_regression_check_pass")

    baseline = _summary(scan_p95=100.0, enrich_sec=10.0, search_p95=50.0, done=1000)
    candidate = _summary(scan_p95=90.0, enrich_sec=9.0, search_p95=45.0, done=1000)
    result = module.run_perf_regression_check(baseline=baseline, candidate=candidate)

    assert result.passed is True
    assert result.perf_regression_bps >= 0


def test_perf_regression_check_fails_when_candidate_is_worse() -> None:
    """후보 성능이 기준선보다 나쁘면 실패해야 한다."""
    script_path = Path(__file__).resolve().parents[3] / "tools" / "quality" / "perf_regression_check.py"
    module = _load_module(script_path, "perf_regression_check_fail")

    baseline = _summary(scan_p95=100.0, enrich_sec=10.0, search_p95=50.0, done=1000)
    candidate = _summary(scan_p95=120.0, enrich_sec=12.0, search_p95=60.0, done=800)
    result = module.run_perf_regression_check(baseline=baseline, candidate=candidate)

    assert result.passed is False
    assert result.perf_regression_bps < 0
