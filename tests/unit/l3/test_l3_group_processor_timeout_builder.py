"""L3GroupProcessor timeout builder 연동 계약을 검증한다."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sari.core.models import FileEnrichJobDTO
from sari.services.collection.l3.l3_group_processor import L3GroupProcessor
from sari.services.collection.perf_trace import PerfTracer


def _job(job_id: str, path: str = "a.py") -> FileEnrichJobDTO:
    return FileEnrichJobDTO(
        job_id=job_id,
        repo_id="r1",
        repo_root="/repo",
        relative_path=path,
        content_hash=f"h-{job_id}",
        priority=1,
        enqueue_source="l3",
        status="RUNNING",
        attempt_count=0,
        last_error=None,
        next_retry_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_l3_group_processor_builds_timeout_failure_result_on_timeout_error() -> None:
    """단일 그룹 처리 중 TimeoutError가 나면 timeout builder 결과를 merge해야 한다."""
    merges: list[object] = []
    built: list[dict[str, object]] = []

    def _process_single_l3_job(_job: FileEnrichJobDTO) -> object:
        raise TimeoutError("simulated timeout")

    def _build_timeout_failure_result(*, job: FileEnrichJobDTO, timeout_sec: float, now_iso: str, group_size: int) -> object:
        built.append(
            {
                "job_id": job.job_id,
                "timeout_sec": timeout_sec,
                "now_iso": now_iso,
                "group_size": group_size,
            }
        )
        return type("R", (), {"dev_error": None, "job_id": job.job_id})()

    processor = L3GroupProcessor(
        lsp_backend=object(),
        l3_executor=ThreadPoolExecutor(max_workers=1),
        perf_tracer=PerfTracer(component="test"),
        resolve_lsp_language=lambda _: None,
        set_group_bulk_mode=lambda *_: None,
        resolve_l3_parallelism=lambda _: 1,
        process_single_l3_job=_process_single_l3_job,
        merge_l3_result=lambda *, result, buffers: merges.append(result),
        flush_l3_buffers=lambda **_: None,
        group_wait_timeout_sec=0.25,
        now_iso_supplier=lambda: "2026-02-26T00:00:00+00:00",
        build_timeout_failure_result=_build_timeout_failure_result,
    )

    processed = processor.process_group(group=[_job("j1")], buffers=object(), body_upserts=[])

    assert processed == 1
    assert len(built) == 1
    assert built[0]["job_id"] == "j1"
    assert built[0]["timeout_sec"] == 0.25
    assert built[0]["group_size"] == 1
    assert len(merges) == 1

