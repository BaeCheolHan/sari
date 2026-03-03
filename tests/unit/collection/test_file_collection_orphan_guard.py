"""파일 수집 워커의 고아 감지 가드를 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.core.exceptions import CollectionError
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.collection.service import FileCollectionService
from sari.services.lsp_extraction_contracts import LspExtractionResultDTO
from sari.services.pipeline.quality_service import PipelineQualityService


class _NoopLspBackend:
    """고아 감지 테스트는 LSP 품질이 아니라 제어 흐름만 검증한다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        _ = (repo_root, relative_path, content_hash)
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


def test_process_enrich_jobs_raises_collection_error_when_orphan_detected(tmp_path: Path) -> None:
    """고아 상태 감지 시 보강 루프는 명시적 오류를 발생시켜야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineQualityService.default_collection_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=PipelinePolicyRepository(db_path),
        parent_alive_probe=lambda: False,
    )

    with pytest.raises(CollectionError) as exc_info:
        service.process_enrich_jobs(limit=1)

    assert exc_info.value.context.code == "ERR_ORPHAN_DETECTED"
