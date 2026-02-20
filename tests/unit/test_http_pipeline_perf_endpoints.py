"""HTTP 성능 엔드포인트 동작을 검증한다."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.schema import init_schema
from sari.http.app import HttpContext, pipeline_perf_report_api_endpoint, pipeline_perf_run_api_endpoint
from sari.search.candidate_search import CandidateSearchService
from sari.search.orchestrator import SearchOrchestrator
from sari.search.symbol_resolve import SymbolResolveService
from sari.services.admin_service import AdminService
from sari.services.pipeline_perf_service import PipelinePerfService
from sari.services.workspace_service import WorkspaceService
from sari.lsp.hub import LspHub


class _FakeBenchmarkService:
    """벤치마크 결과를 고정 반환한다."""

    def run(
        self,
        repo_root: str,
        target_files: int,
        profile: str,
        language_filter: tuple[str, ...] | None = None,
        per_language_report: bool = False,
    ) -> dict[str, object]:
        """샘플 벤치 요약을 반환한다."""
        del repo_root, profile, language_filter, per_language_report
        return {
            "status": "COMPLETED",
            "target_files": target_files,
            "scan": {"ingest_latency_ms_p95": 1000},
            "enrich": {"completion_sec": 8.0, "done_count": 2000, "dead_count": 0},
        }


class _FakeQueueRepository:
    """큐 상태를 고정 반환한다."""

    def get_status_counts(self) -> dict[str, int]:
        """큐 상태를 반환한다."""
        return {"PENDING": 0, "RUNNING": 0, "FAILED": 0, "DONE": 1000, "DEAD": 0}


class _FakeCollectionService:
    """scan/process를 흉내낸다."""

    def scan_once(self, repo_root: str):  # noqa: ANN201
        """스캔 결과 더미를 반환한다."""
        del repo_root
        return type("ScanResult", (), {"scanned_count": 1000, "indexed_count": 1000, "deleted_count": 0})()

    def process_enrich_jobs(self, limit: int) -> int:
        """큐가 이미 비어있다고 가정한다."""
        del limit
        return 0


def _build_context(tmp_path: Path) -> HttpContext:
    """성능 API 테스트용 HTTP 컨텍스트를 구성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    runtime_repo = RuntimeRepository(db_path)
    symbol_cache_repo = SymbolCacheRepository(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    WorkspaceService(workspace_repo).add_workspace(str(repo_dir.resolve()))

    perf_service = PipelinePerfService(
        file_collection_service=_FakeCollectionService(),
        queue_repo=_FakeQueueRepository(),
        benchmark_service=_FakeBenchmarkService(),
        perf_repo=PipelinePerfRepository(db_path),
        artifact_root=tmp_path / "artifacts",
    )
    orchestrator = SearchOrchestrator(
        workspace_repo=workspace_repo,
        candidate_service=CandidateSearchService.build_default(
            max_file_size_bytes=512 * 1024,
            index_root=tmp_path / "candidate_index",
            backend_mode="scan",
            enable_scan_fallback=False,
        ),
        symbol_service=SymbolResolveService(hub=LspHub(), cache_repo=symbol_cache_repo),
    )
    admin_service = AdminService(
        config=AppConfig(db_path=db_path, host="127.0.0.1", preferred_port=47777, max_port_scan=50, stop_grace_sec=10),
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=symbol_cache_repo,
    )
    return HttpContext(
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        search_orchestrator=orchestrator,
        admin_service=admin_service,
        pipeline_perf_service=perf_service,
    )


def test_pipeline_perf_run_and_report_endpoint(tmp_path: Path) -> None:
    """성능 실행/리포트 API가 정상 응답을 반환해야 한다."""
    context = _build_context(tmp_path)
    run_request = SimpleNamespace(
        query_params={
            "repo": "repo-a",
            "target_files": "2000",
            "profile": "realistic_v1",
            "dataset_mode": "isolated",
            "fresh_db": "true",
            "reset_probe_state": "true",
            "cold_lsp_reset": "true",
        },
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    run_response = asyncio.run(pipeline_perf_run_api_endpoint(run_request))
    assert run_response.status_code == 200
    run_payload = json.loads(run_response.body.decode("utf-8"))
    assert run_payload["perf"]["status"] == "COMPLETED"
    assert run_payload["perf"]["dataset_mode"] == "isolated"
    workspace = next(item for item in run_payload["perf"]["datasets"] if item["dataset_type"] == "workspace_real")
    assert workspace["run_context"]["fresh_db"] is True
    assert workspace["run_context"]["pre_state_reset"] is True
    assert workspace["run_context"]["cold_lsp_reset"] is True

    report_request = SimpleNamespace(
        query_params={"repo": "repo-a"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    report_response = asyncio.run(pipeline_perf_report_api_endpoint(report_request))
    assert report_response.status_code == 200
    report_payload = json.loads(report_response.body.decode("utf-8"))
    assert report_payload["perf"]["status"] == "COMPLETED"
