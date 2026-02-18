"""HTTP 계층 공용 컨텍스트를 정의한다."""

from __future__ import annotations

from pathlib import Path

from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.search.orchestrator import SearchOrchestrator
from sari.services.admin_service import AdminService
from sari.services.collection.ports import CollectionRuntimePort
from sari.services.pipeline_benchmark_service import PipelineBenchmarkService
from sari.services.pipeline_control_service import PipelineControlService
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline_quality_service import PipelineQualityService
from sari.services.read_facade_service import ReadFacadeService


class HttpContext:
    """HTTP 엔드포인트가 공유하는 런타임 의존성을 보관한다."""

    def __init__(
        self,
        runtime_repo: RuntimeRepository,
        workspace_repo: WorkspaceRepository,
        search_orchestrator: SearchOrchestrator,
        admin_service: AdminService,
        file_collection_service: CollectionRuntimePort | None = None,
        pipeline_control_service: PipelineControlService | None = None,
        pipeline_benchmark_service: PipelineBenchmarkService | None = None,
        pipeline_quality_service: PipelineQualityService | None = None,
        pipeline_lsp_matrix_service: PipelineLspMatrixService | None = None,
        read_facade_service: ReadFacadeService | None = None,
        language_probe_repo: LanguageProbeRepository | None = None,
        db_path: Path | None = None,
    ) -> None:
        """HTTP 계층에서 사용하는 서비스 집합을 초기화한다."""
        self.runtime_repo = runtime_repo
        self.workspace_repo = workspace_repo
        self.search_orchestrator = search_orchestrator
        self.admin_service = admin_service
        self.file_collection_service = file_collection_service
        self.pipeline_control_service = pipeline_control_service
        self.pipeline_benchmark_service = pipeline_benchmark_service
        self.pipeline_quality_service = pipeline_quality_service
        self.pipeline_lsp_matrix_service = pipeline_lsp_matrix_service
        self.read_facade_service = read_facade_service
        self.language_probe_repo = language_probe_repo
        self.db_path = db_path
