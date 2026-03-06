from __future__ import annotations

from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.l5.solid_lsp_extraction_backend import SolidLspExtractionBackend
from sari.services.collection.service import build_default_file_collection_service


class _FakeHub:
    def get_metrics(self) -> dict[str, int]:
        return {}


def test_build_default_file_collection_service_attaches_repo_probe_repo_to_supplied_backend(tmp_path) -> None:  # noqa: ANN001
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_probe_repo = RepoLanguageProbeRepository(db_path)
    backend = SolidLspExtractionBackend(hub=_FakeHub(), repo_language_probe_repo=None)  # type: ignore[arg-type]

    service = build_default_file_collection_service(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        error_event_repo=PipelineErrorEventRepository(db_path),
        lsp_backend=backend,
        repo_language_probe_repo=repo_probe_repo,
    )
    service.stop_background()

    assert backend._repo_language_probe_repo is repo_probe_repo  # type: ignore[attr-defined]
