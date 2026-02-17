"""파이프라인 메트릭 전용 컴포넌트."""

from __future__ import annotations

import math
from threading import Lock
from typing import Callable

from sari.core.models import PipelineMetricsDTO


class PipelineMetricsService:
    """진행률/ETA/처리량 계산 책임을 담당한다."""

    def __init__(
        self,
        *,
        refresh_indexing_mode: Callable[[], None],
        enrich_queue_repo: object,
        file_repo: object,
        l3_queue_size: Callable[[], int],
        metrics_lock: Lock,
        enrich_latency_samples_ms: list[float],
        throughput_samples_jobs_per_sec: list[float],
        get_throughput_ema: Callable[[], float],
        set_throughput_ema: Callable[[float], None],
        throughput_alpha: float,
        enrich_threads_count: Callable[[], int],
        compute_coverage_bps: Callable[[], tuple[int, int]],
        indexing_mode: Callable[[], str],
        worker_state: Callable[[], str],
        last_error_code: Callable[[], str | None],
        last_error_message: Callable[[], str | None],
        last_error_at: Callable[[], str | None],
    ) -> None:
        """메트릭 계산에 필요한 의존성만 주입받는다."""
        self._refresh_indexing_mode = refresh_indexing_mode
        self._enrich_queue_repo = enrich_queue_repo
        self._file_repo = file_repo
        self._l3_queue_size = l3_queue_size
        self._metrics_lock = metrics_lock
        self._enrich_latency_samples_ms = enrich_latency_samples_ms
        self._throughput_samples_jobs_per_sec = throughput_samples_jobs_per_sec
        self._get_throughput_ema = get_throughput_ema
        self._set_throughput_ema = set_throughput_ema
        self._throughput_alpha = throughput_alpha
        self._enrich_threads_count = enrich_threads_count
        self._compute_coverage_bps = compute_coverage_bps
        self._indexing_mode = indexing_mode
        self._worker_state = worker_state
        self._last_error_code = last_error_code
        self._last_error_message = last_error_message
        self._last_error_at = last_error_at

    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        """파이프라인 실시간 메트릭을 반환한다."""
        self._refresh_indexing_mode()
        counts = self._enrich_queue_repo.get_status_counts()
        queue_depth = int(counts.get("PENDING", 0) + counts.get("FAILED", 0))
        running_jobs = int(counts.get("RUNNING", 0))
        failed_jobs = int(counts.get("FAILED", 0))
        dead_jobs = int(counts.get("DEAD", 0))
        done_jobs = int(counts.get("DONE", 0))
        l2_coverage_bps, l3_coverage_bps = self._compute_coverage_bps()
        state_counts = self._file_repo.get_enrich_state_counts()
        total_files = int(sum(state_counts.values()))
        l2_ready_count = int(state_counts.get("BODY_READY", 0)) + int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        l3_ready_count = int(state_counts.get("LSP_READY", 0)) + int(state_counts.get("TOOL_READY", 0))
        remaining_jobs_l2 = max(0, total_files - l2_ready_count)
        remaining_jobs_l3 = max(0, total_files - l3_ready_count)
        progress_percent_l2 = float(l2_coverage_bps) / 100.0
        progress_percent_l3 = float(l3_coverage_bps) / 100.0
        l3_backlog_count = int(self._l3_queue_size())
        with self._metrics_lock:
            avg_latency = 0.0
            if len(self._enrich_latency_samples_ms) > 0:
                avg_latency = float(sum(self._enrich_latency_samples_ms) / len(self._enrich_latency_samples_ms))
            throughput_ema = float(self._get_throughput_ema())
            throughput_samples = list(self._throughput_samples_jobs_per_sec)
        worker_count = max(1, self._enrich_threads_count())
        jobs_per_sec = 0.0
        if avg_latency > 0:
            jobs_per_sec = float(worker_count) / (avg_latency / 1000.0)
        eta_basis = throughput_ema if throughput_ema > 0.0 else jobs_per_sec
        eta_l2_sec = -1
        eta_l3_sec = -1
        eta_confidence_bps = 0
        eta_window_sec = len(throughput_samples)
        if len(throughput_samples) >= 8:
            stable_samples = sorted(throughput_samples)[-8:]
            min_s = min(stable_samples)
            max_s = max(stable_samples)
            if max_s > 0:
                ratio = min_s / max_s
                eta_confidence_bps = int(max(0.0, min(1.0, ratio)) * 10000.0)
        if remaining_jobs_l2 == 0:
            eta_l2_sec = 0
        elif eta_basis > 0 and eta_confidence_bps >= 5000:
            eta_l2_sec = int(math.ceil(float(remaining_jobs_l2) / eta_basis))
        if remaining_jobs_l3 == 0:
            eta_l3_sec = 0
        elif eta_basis > 0 and eta_confidence_bps >= 5000:
            eta_l3_sec = int(math.ceil(float(remaining_jobs_l3) / eta_basis))
        return PipelineMetricsDTO(
            queue_depth=queue_depth,
            running_jobs=running_jobs,
            failed_jobs=failed_jobs,
            dead_jobs=dead_jobs,
            done_jobs=done_jobs,
            avg_enrich_latency_ms=avg_latency,
            indexing_mode=self._indexing_mode(),
            l2_coverage_bps=l2_coverage_bps,
            l3_coverage_bps=l3_coverage_bps,
            l3_backlog_count=l3_backlog_count,
            progress_percent_l2=progress_percent_l2,
            progress_percent_l3=progress_percent_l3,
            eta_l2_sec=eta_l2_sec,
            eta_l3_sec=eta_l3_sec,
            eta_confidence_bps=eta_confidence_bps,
            eta_window_sec=eta_window_sec,
            throughput_ema=throughput_ema,
            remaining_jobs_l2=remaining_jobs_l2,
            remaining_jobs_l3=remaining_jobs_l3,
            worker_state=self._worker_state(),
            last_error_code=self._last_error_code(),
            last_error_message=self._last_error_message(),
            last_error_at=self._last_error_at(),
        )

    def record_enrich_latency(self, latency_ms: float) -> None:
        """단건 처리 지연시간 샘플을 기록한다."""
        with self._metrics_lock:
            self._enrich_latency_samples_ms.append(latency_ms)
            if len(self._enrich_latency_samples_ms) > 200:
                del self._enrich_latency_samples_ms[:-200]
            if latency_ms > 0:
                instant_jobs_per_sec = 1000.0 / latency_ms
                self._throughput_samples_jobs_per_sec.append(instant_jobs_per_sec)
                if len(self._throughput_samples_jobs_per_sec) > 200:
                    del self._throughput_samples_jobs_per_sec[:-200]
                throughput_ema = self._get_throughput_ema()
                if throughput_ema <= 0.0:
                    self._set_throughput_ema(instant_jobs_per_sec)
                else:
                    alpha = self._throughput_alpha
                    self._set_throughput_ema(alpha * instant_jobs_per_sec + (1.0 - alpha) * throughput_ema)
