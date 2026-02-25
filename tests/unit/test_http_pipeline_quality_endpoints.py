"""HTTP 품질 엔드포인트 동작을 검증한다."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

from sari.core.config import AppConfig
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.http.app import (
    HttpContext,
    pipeline_quality_report_api_endpoint,
    pipeline_quality_run_api_endpoint,
)
from sari.lsp.hub import LspHub
from sari.search.candidate_search import CandidateSearchService
from sari.search.orchestrator import SearchOrchestrator
from sari.search.symbol_resolve import SymbolResolveService
from sari.services.admin.service import AdminService
from sari.services.collection.service import FileCollectionService
from sari.services.pipeline.quality_service import MirrorGoldenBackend, PipelineQualityService
from sari.services.workspace.service import WorkspaceService


def _default_context(tmp_path: Path) -> HttpContext:
    """품질 API 테스트용 HTTP 컨텍스트를 구성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    workspace_repo = WorkspaceRepository(db_path)
    runtime_repo = RuntimeRepository(db_path)
    symbol_cache_repo = SymbolCacheRepository(db_path)
    lsp_repo = LspToolDataRepository(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    WorkspaceService(workspace_repo).add_workspace(str(repo_dir.resolve()))

    collection_service = FileCollectionService(
        workspace_repo=workspace_repo,
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=lsp_repo,
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineQualityService.default_collection_policy(),
        lsp_backend=MirrorGoldenBackend(),
        policy_repo=None,
        event_repo=None,
    )
    collection_service.scan_once(str(repo_dir.resolve()))
    collection_service.process_enrich_jobs(limit=20)

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=lsp_repo,
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=MirrorGoldenBackend(),
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
        file_collection_service=collection_service,
        pipeline_control_service=None,
        pipeline_quality_service=quality_service,
    )


def test_pipeline_quality_run_and_report_endpoint(tmp_path: Path) -> None:
    """품질 실행/리포트 API가 정상 응답을 반환해야 한다."""
    context = _default_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": "repo-a", "limit_files": "100", "profile": "default"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(pipeline_quality_run_api_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["quality"]["status"] in {"PASSED", "FAILED"}

    report_request = SimpleNamespace(
        query_params={"repo": "repo-a"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    report_response = asyncio.run(pipeline_quality_report_api_endpoint(report_request))
    assert report_response.status_code == 200
    report_payload = json.loads(report_response.body.decode("utf-8"))
    assert "quality" in report_payload
