"""벤치마크 요약 JSON 간 성능 회귀 여부를 판정한다."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PerfMetricComparisonDTO:
    """단일 성능 지표 비교 결과를 표현한다."""

    metric: str
    baseline: float
    candidate: float
    regression_bps: int

    def to_dict(self) -> dict[str, object]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "regression_bps": self.regression_bps,
        }


@dataclass(frozen=True)
class PerfGateResultDTO:
    """성능 회귀 게이트 결과를 표현한다."""

    perf_regression_bps: int
    passed: bool
    comparisons: list[PerfMetricComparisonDTO]

    def to_dict(self) -> dict[str, object]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "perf_regression_bps": self.perf_regression_bps,
            "passed": self.passed,
            "comparisons": [item.to_dict() for item in self.comparisons],
        }


def _read_json(path: Path) -> dict[str, object]:
    """JSON 파일을 읽어 딕셔너리로 반환한다."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("summary JSON은 object 형태여야 합니다")
    return loaded


def _nested_float(summary: dict[str, object], section: str, field: str) -> float:
    """요약 JSON에서 숫자 필드를 안전하게 추출한다."""
    section_raw = summary.get(section)
    if not isinstance(section_raw, dict):
        raise ValueError(f"missing section: {section}")
    value = section_raw.get(field)
    if not isinstance(value, (int, float)):
        raise ValueError(f"missing numeric field: {section}.{field}")
    return float(value)


def _throughput(summary: dict[str, object]) -> float:
    """done_count/completion_sec 기반 처리량을 계산한다."""
    enrich = summary.get("enrich")
    if not isinstance(enrich, dict):
        raise ValueError("missing section: enrich")
    done = enrich.get("done_count")
    sec = enrich.get("completion_sec")
    if not isinstance(done, int) or not isinstance(sec, (int, float)):
        raise ValueError("missing numeric fields: enrich.done_count/enrich.completion_sec")
    sec_value = float(sec)
    if sec_value <= 0.0:
        return 0.0
    return float(done) / sec_value


def _regression_bps_lower_is_better(baseline: float, candidate: float) -> int:
    """작을수록 좋은 지표의 회귀율(bps)을 계산한다."""
    if baseline <= 0.0:
        return 0
    return int(round(((baseline - candidate) / baseline) * 10_000.0))


def _regression_bps_higher_is_better(baseline: float, candidate: float) -> int:
    """클수록 좋은 지표의 회귀율(bps)을 계산한다."""
    if baseline <= 0.0:
        return 0
    return int(round(((candidate - baseline) / baseline) * 10_000.0))


def run_perf_regression_check(baseline: dict[str, object], candidate: dict[str, object]) -> PerfGateResultDTO:
    """기준선/후보 요약을 비교해 회귀 게이트 결과를 반환한다."""
    comparisons: list[PerfMetricComparisonDTO] = []

    baseline_ingest = _nested_float(baseline, "scan", "ingest_latency_ms_p95")
    candidate_ingest = _nested_float(candidate, "scan", "ingest_latency_ms_p95")
    comparisons.append(
        PerfMetricComparisonDTO(
            metric="scan.ingest_latency_ms_p95",
            baseline=baseline_ingest,
            candidate=candidate_ingest,
            regression_bps=_regression_bps_lower_is_better(baseline_ingest, candidate_ingest),
        )
    )

    baseline_enrich = _nested_float(baseline, "enrich", "completion_sec")
    candidate_enrich = _nested_float(candidate, "enrich", "completion_sec")
    comparisons.append(
        PerfMetricComparisonDTO(
            metric="enrich.completion_sec",
            baseline=baseline_enrich,
            candidate=candidate_enrich,
            regression_bps=_regression_bps_lower_is_better(baseline_enrich, candidate_enrich),
        )
    )

    baseline_search = _nested_float(baseline, "search", "search_latency_ms_p95")
    candidate_search = _nested_float(candidate, "search", "search_latency_ms_p95")
    comparisons.append(
        PerfMetricComparisonDTO(
            metric="search.search_latency_ms_p95",
            baseline=baseline_search,
            candidate=candidate_search,
            regression_bps=_regression_bps_lower_is_better(baseline_search, candidate_search),
        )
    )

    baseline_tp = _throughput(baseline)
    candidate_tp = _throughput(candidate)
    comparisons.append(
        PerfMetricComparisonDTO(
            metric="enrich.throughput_jobs_per_sec",
            baseline=baseline_tp,
            candidate=candidate_tp,
            regression_bps=_regression_bps_higher_is_better(baseline_tp, candidate_tp),
        )
    )

    regression_bps = min(item.regression_bps for item in comparisons) if len(comparisons) > 0 else 0
    return PerfGateResultDTO(
        perf_regression_bps=regression_bps,
        passed=regression_bps >= 0,
        comparisons=comparisons,
    )


def main() -> int:
    """CLI 진입점이다."""
    parser = argparse.ArgumentParser(prog="perf_regression_check")
    parser.add_argument("--baseline", required=True, help="기준선 summary JSON 경로")
    parser.add_argument("--candidate", required=True, help="후보 summary JSON 경로")
    parser.add_argument("--output-json", required=False, default="", help="JSON 결과 파일")
    args = parser.parse_args()

    baseline_path = Path(args.baseline).expanduser().resolve()
    candidate_path = Path(args.candidate).expanduser().resolve()
    if not baseline_path.exists() or not candidate_path.exists():
        raise SystemExit("baseline/candidate 파일을 찾을 수 없습니다")

    result = run_perf_regression_check(_read_json(baseline_path), _read_json(candidate_path))
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if str(args.output_json).strip() != "":
        out_path = Path(str(args.output_json)).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
