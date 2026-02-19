"""파이프라인 성능 실측 서비스를 구현한다."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sari.core.exceptions import ErrorContext, PerfError
from sari.core.models import now_iso8601_utc
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository


class PipelinePerfService:
    """혼합지표(L3 처리량/총시간/실패율) 성능 실측을 수행한다."""

    def __init__(
        self,
        file_collection_service: object,
        queue_repo: object,
        benchmark_service: object,
        perf_repo: PipelinePerfRepository,
        artifact_root: Path,
    ) -> None:
        """실측 서비스 의존성을 주입한다."""
        self._file_collection_service = file_collection_service
        self._queue_repo = queue_repo
        self._benchmark_service = benchmark_service
        self._perf_repo = perf_repo
        self._artifact_root = artifact_root

    def run(self, repo_root: str, target_files: int, profile: str, dataset_mode: str = "isolated") -> dict[str, object]:
        """샘플 2k + 실데이터 2트랙 실측을 실행하고 요약을 반환한다."""
        root = Path(repo_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise PerfError(ErrorContext(code="ERR_REPO_NOT_FOUND", message="repo 경로를 찾을 수 없습니다"))
        if target_files <= 0:
            raise PerfError(ErrorContext(code="ERR_INVALID_TARGET_FILES", message="target_files는 1 이상이어야 합니다"))
        if profile.strip() == "":
            raise PerfError(ErrorContext(code="ERR_INVALID_PROFILE", message="profile은 비어 있을 수 없습니다"))
        normalized_dataset_mode = dataset_mode.strip().lower()
        if normalized_dataset_mode not in ("isolated", "legacy"):
            raise PerfError(ErrorContext(code="ERR_INVALID_DATASET_MODE", message="dataset_mode must be isolated or legacy"))

        started_at = now_iso8601_utc()
        run_id = self._perf_repo.create_run(
            repo_root=str(root),
            target_files=target_files,
            profile=profile,
            started_at=started_at,
        )
        try:
            if normalized_dataset_mode == "isolated":
                workspace_dataset = self._measure_workspace_dataset(repo_root=str(root), dataset_mode=normalized_dataset_mode)
                sample_dataset = self._measure_sample_dataset(repo_root=str(root), target_files=target_files, dataset_mode=normalized_dataset_mode)
                datasets = [workspace_dataset, sample_dataset]
            else:
                sample_dataset = self._measure_sample_dataset(repo_root=str(root), target_files=target_files, dataset_mode=normalized_dataset_mode)
                workspace_dataset = self._measure_workspace_dataset(repo_root=str(root), dataset_mode=normalized_dataset_mode)
                datasets = [sample_dataset, workspace_dataset]
            gate_passed = all(bool(item.get("gate_passed")) for item in datasets)
            summary: dict[str, object] = {
                "run_id": run_id,
                "status": "COMPLETED",
                "repo_root": str(root),
                "threshold_profile": profile,
                "dataset_mode": normalized_dataset_mode,
                "target_files": target_files,
                "gate_passed": gate_passed,
                "datasets": datasets,
            }
            self._write_artifact(run_id=run_id, summary=summary)
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="COMPLETED",
                summary=summary,
            )
            return summary
        except PerfError as exc:
            failed = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "dataset_mode": normalized_dataset_mode,
                "threshold_profile": profile,
                "error": str(exc),
            }
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed,
            )
            raise
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            failed = {
                "run_id": run_id,
                "status": "FAILED",
                "repo_root": str(root),
                "target_files": target_files,
                "dataset_mode": normalized_dataset_mode,
                "threshold_profile": profile,
                "error": str(exc),
            }
            self._perf_repo.complete_run(
                run_id=run_id,
                finished_at=now_iso8601_utc(),
                status="FAILED",
                summary=failed,
            )
            raise PerfError(ErrorContext(code="ERR_PERF_RUN_FAILED", message=f"perf run failed: {exc}")) from exc

    def get_latest_report(self) -> dict[str, object]:
        """최신 성능 실측 리포트를 반환한다."""
        latest = self._perf_repo.get_latest_run()
        if latest is None:
            raise PerfError(ErrorContext(code="ERR_PERF_NOT_FOUND", message="no perf run found"))
        summary = latest.get("summary")
        if isinstance(summary, dict):
            return summary
        return latest

    def _measure_sample_dataset(self, repo_root: str, target_files: int, dataset_mode: str) -> dict[str, object]:
        """샘플 2k 기준 실측 지표를 계산한다."""
        summary = self._benchmark_service.run(
            repo_root=repo_root,
            target_files=target_files,
            profile="default",
            language_filter=None,
            per_language_report=False,
        )
        scan = summary.get("scan", {})
        enrich = summary.get("enrich", {})
        ingest_ms = float(scan.get("ingest_latency_ms_p95", 0.0))
        enrich_sec = float(enrich.get("completion_sec", 0.0))
        done_count = int(enrich.get("done_count", 0))
        dead_count = int(enrich.get("dead_count", 0))
        wall_time_sec = (ingest_ms / 1000.0) + enrich_sec
        return self._build_dataset_result(
            dataset_type="sample_2k",
            repo_scope=repo_root,
            done_count=done_count,
            dead_count=dead_count,
            l3_elapsed_sec=enrich_sec,
            wall_time_sec=wall_time_sec,
            count_mode="summary",
            start_counts=None,
            end_counts=None,
            measurement_scope=f"sample_2k_{dataset_mode}",
            run_context={"fresh_db": False, "pre_state_reset": False},
        )

    def _measure_workspace_dataset(self, repo_root: str, dataset_mode: str) -> dict[str, object]:
        """실데이터 기준 실측 지표를 계산한다."""
        start_counts = self._queue_counts_snapshot()
        scan_started = time.perf_counter()
        self._file_collection_service.scan_once(repo_root=repo_root)
        scan_elapsed_sec = float(time.perf_counter() - scan_started)
        enrich_started = time.perf_counter()
        self._drain_enrich_queue(max_wait_sec=120.0)
        enrich_elapsed_sec = float(time.perf_counter() - enrich_started)
        end_counts = self._queue_counts_snapshot()
        done_count = max(0, int(end_counts.get("DONE", 0)) - int(start_counts.get("DONE", 0)))
        dead_count = max(0, int(end_counts.get("DEAD", 0)) - int(start_counts.get("DEAD", 0)))
        wall_time_sec = scan_elapsed_sec + enrich_elapsed_sec
        return self._build_dataset_result(
            dataset_type="workspace_real",
            repo_scope=repo_root,
            done_count=done_count,
            dead_count=dead_count,
            l3_elapsed_sec=enrich_elapsed_sec,
            wall_time_sec=wall_time_sec,
            count_mode="delta",
            start_counts=start_counts,
            end_counts=end_counts,
            measurement_scope=f"workspace_real_{dataset_mode}",
            run_context={"fresh_db": False, "pre_state_reset": False},
        )

    def _drain_enrich_queue(self, max_wait_sec: float) -> None:
        """큐가 비워질 때까지 보강 작업을 반복 실행한다."""
        deadline = time.time() + max_wait_sec
        while time.time() < deadline:
            processed = int(self._file_collection_service.process_enrich_jobs(limit=100))
            counts = self._queue_repo.get_status_counts()
            pending = int(counts.get("PENDING", 0))
            running = int(counts.get("RUNNING", 0))
            if processed == 0 and pending == 0 and running == 0:
                return
            if processed == 0:
                time.sleep(0.02)
        raise PerfError(ErrorContext(code="ERR_PERF_TIMEOUT", message="perf queue drain timeout"))

    def _queue_counts_snapshot(self) -> dict[str, int]:
        """큐 상태 카운트를 정규화해 스냅샷으로 반환한다."""
        raw_counts = self._queue_repo.get_status_counts()
        return {
            "PENDING": int(raw_counts.get("PENDING", 0)),
            "RUNNING": int(raw_counts.get("RUNNING", 0)),
            "FAILED": int(raw_counts.get("FAILED", 0)),
            "DONE": int(raw_counts.get("DONE", 0)),
            "DEAD": int(raw_counts.get("DEAD", 0)),
        }

    def _build_dataset_result(
        self,
        dataset_type: str,
        repo_scope: str,
        done_count: int,
        dead_count: int,
        l3_elapsed_sec: float,
        wall_time_sec: float,
        count_mode: str,
        start_counts: dict[str, int] | None,
        end_counts: dict[str, int] | None,
        measurement_scope: str,
        run_context: dict[str, object],
    ) -> dict[str, object]:
        """단일 데이터셋 실측 결과와 게이트 판정을 생성한다."""
        denominator = done_count + dead_count
        error_rate = 0.0 if denominator == 0 else (float(dead_count) / float(denominator)) * 100.0
        l3_jobs_per_sec = 0.0 if l3_elapsed_sec <= 0 else float(done_count) / l3_elapsed_sec
        gate_passed = bool(l3_jobs_per_sec >= 220.0 and wall_time_sec <= 13.0 and error_rate <= 0.5)
        return {
            "dataset_type": dataset_type,
            "repo_scope": repo_scope,
            "count_mode": count_mode,
            "measurement_scope": measurement_scope,
            "run_context": run_context,
            "start_counts": start_counts,
            "end_counts": end_counts,
            "done_count": done_count,
            "dead_count": dead_count,
            "l3_jobs_per_sec": round(l3_jobs_per_sec, 4),
            "wall_time_sec": round(wall_time_sec, 4),
            "error_rate": round(error_rate, 4),
            "gate_passed": gate_passed,
        }

    def _write_artifact(self, run_id: str, summary: dict[str, object]) -> None:
        """실측 아티팩트를 파일로 저장한다."""
        perf_dir = self._artifact_root / "perf"
        perf_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = perf_dir / f"{run_id}.json"
        artifact_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
