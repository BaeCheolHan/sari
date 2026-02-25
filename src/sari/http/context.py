"""HTTP 계층 공용 컨텍스트를 정의한다."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable

from sari.http.ports import LanguageProbeRepoPort, RepoRegistryRepoPort, RuntimeRepoPort, WorkspaceRepoPort
from sari.search.orchestrator import SearchOrchestrator
from sari.services.admin import AdminService
from sari.services.collection.ports import CollectionRuntimePort
from sari.services.http.presentation_service import HttpPresentationService
from sari.services.pipeline.control_service import PipelineControlService
from sari.services.pipeline.lsp_matrix_ports import PipelineLspMatrixPort
from sari.services.pipeline.perf_service import PipelinePerfService
from sari.services.pipeline.quality_service import PipelineQualityService
from sari.services.read.facade_service import ReadFacadeService


class HttpContext:
    """HTTP 엔드포인트가 공유하는 런타임 의존성을 보관한다."""

    def __init__(
        self,
        runtime_repo: RuntimeRepoPort,
        workspace_repo: WorkspaceRepoPort,
        search_orchestrator: SearchOrchestrator,
        admin_service: AdminService,
        file_collection_service: CollectionRuntimePort | None = None,
        pipeline_control_service: PipelineControlService | None = None,
        pipeline_perf_service: PipelinePerfService | None = None,
        pipeline_quality_service: PipelineQualityService | None = None,
        pipeline_lsp_matrix_service: PipelineLspMatrixPort | None = None,
        read_facade_service: ReadFacadeService | None = None,
        language_probe_repo: LanguageProbeRepoPort | None = None,
        repo_registry_repo: RepoRegistryRepoPort | None = None,
        lsp_metrics_provider: Callable[[], dict[str, int]] | None = None,
        search_resolve_symbols_default_provider: Callable[[], bool] | None = None,
        http_presentation_service: HttpPresentationService | None = None,
        db_path: Path | None = None,
        http_bg_proxy_enabled: bool = False,
        http_bg_proxy_target: str = "",
    ) -> None:
        """HTTP 계층에서 사용하는 서비스 집합을 초기화한다."""
        self.runtime_repo = runtime_repo
        self.workspace_repo = workspace_repo
        self.search_orchestrator = search_orchestrator
        self.admin_service = admin_service
        self.file_collection_service = file_collection_service
        self.pipeline_control_service = pipeline_control_service
        self.pipeline_perf_service = pipeline_perf_service
        self.pipeline_quality_service = pipeline_quality_service
        self.pipeline_lsp_matrix_service = pipeline_lsp_matrix_service
        self.read_facade_service = read_facade_service
        self.language_probe_repo = language_probe_repo
        self.repo_registry_repo = repo_registry_repo
        self.lsp_metrics_provider = lsp_metrics_provider
        self.search_resolve_symbols_default_provider = search_resolve_symbols_default_provider
        if http_presentation_service is None:
            self.http_presentation_service = self._create_http_presentation_service(db_path=db_path)
        else:
            self.http_presentation_service = http_presentation_service
        self.db_path = db_path
        self.http_bg_proxy_enabled = http_bg_proxy_enabled
        self.http_bg_proxy_target = http_bg_proxy_target

    def resolve_http_presentation_service(self) -> HttpPresentationService:
        """현재 컨텍스트에 맞는 프레젠테이션 서비스를 반환한다."""
        if self.http_presentation_service.supports_tool_layer_snapshot:
            return self.http_presentation_service
        if self.db_path is None:
            return self.http_presentation_service
        self.http_presentation_service = self._create_http_presentation_service(db_path=self.db_path)
        return self.http_presentation_service

    def _create_http_presentation_service(self, *, db_path: Path | None) -> HttpPresentationService:
        tool_layer_repo = None
        if db_path is not None:
            module = importlib.import_module("sari.db.repositories.tool_data_layer_repository")
            repo_cls = getattr(module, "ToolDataLayerRepository")
            tool_layer_repo = repo_cls(db_path)
        return HttpPresentationService(
            workspace_repo=self.workspace_repo,
            language_probe_repo=self.language_probe_repo,
            tool_layer_repo=tool_layer_repo,
        )
