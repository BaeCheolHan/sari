"""파이프라인 품질 서비스 동작을 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from sari.core.exceptions import QualityError
from sari.core.models import CollectionPolicyDTO, L3ReferenceDataDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.file_collection_service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO
from sari.services.pipeline_quality_service import PipelineQualityService, SerenaGoldenBackend
from sari.services.workspace_service import WorkspaceService
from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException


class _GoldenBackend(LspExtractionBackend):
    """테스트용 골든 백엔드다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """파일 단위 골든 심볼/호출자를 반환한다."""
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(
            symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 2}],
            relations=[{"from_symbol": "main", "to_symbol": "alpha", "line": 3}],
            error_message=None,
        )


def _default_policy() -> CollectionPolicyDTO:
    """테스트용 수집 정책을 반환한다."""
    return CollectionPolicyDTO(
        include_ext=(".py",),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )


def test_pipeline_quality_service_runs_and_returns_metrics(tmp_path: Path) -> None:
    """품질 실행은 precision/error_rate를 포함한 요약을 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    file_path = repo_dir / "a.py"
    file_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_default_policy(),
        lsp_backend=_GoldenBackend(),
        policy_repo=None,
        event_repo=None,
    )
    collection_service.scan_once(str(repo_dir))
    collection_service.process_enrich_jobs(limit=20)

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )

    summary = quality_service.run(repo_root=str(repo_dir), limit_files=50, profile="default")

    assert summary["status"] == "PASSED"
    assert summary["precision"]["total"] >= 95.0
    assert summary["error_rate"] <= 1.0


def test_pipeline_quality_service_raises_for_empty_dataset(tmp_path: Path) -> None:
    """인덱싱된 파일이 없으면 명시적 오류를 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(QualityError, match="index된 파일이 없습니다"):
        quality_service.run(repo_root=str(repo_dir), limit_files=10, profile="default")


def test_pipeline_quality_service_supports_language_filter(tmp_path: Path) -> None:
    """품질 실행은 language_filter 옵션을 적용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    file_path = repo_dir / "a.py"
    file_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    WorkspaceService(WorkspaceRepository(db_path)).add_workspace(str(repo_dir))

    collection_service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_default_policy(),
        lsp_backend=_GoldenBackend(),
        policy_repo=None,
        event_repo=None,
    )
    collection_service.scan_once(str(repo_dir))
    collection_service.process_enrich_jobs(limit=20)

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )
    summary = quality_service.run(
        repo_root=str(repo_dir),
        limit_files=50,
        profile="default",
        language_filter=("python",),
    )

    assert summary["language_filter"] == ["python"]
    assert summary["evaluated_files"] >= 1


def test_serena_golden_backend_collects_fallback_reason_stats() -> None:
    """품질 전용 fallback 발생 시 reason 카운트가 통계에 기록되어야 한다."""

    class _FailingDocumentSymbols:
        """iter_symbols 호출 시 실패를 발생시키는 더블이다."""

        def iter_symbols(self) -> list[dict[str, object]]:
            raise SolidLSPException("forced doc symbol failure")

    class _FallbackLsp:
        """documentSymbol 실패 후 workspaceSymbol로 대체 가능한 더블이다."""

        def request_document_symbols(self, relative_path: str) -> _FailingDocumentSymbols:
            del relative_path
            return _FailingDocumentSymbols()

        def request_workspace_symbol(self, query: str) -> list[dict[str, object]]:
            del query
            return []

    class _Hub:
        """고정 LSP 인스턴스를 반환하는 허브 더블이다."""

        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str) -> _FallbackLsp:
            del language, repo_root
            return _FallbackLsp()

    backend = SerenaGoldenBackend(hub=_Hub())  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="a.py", content_hash="hash")
    assert result.error_message is not None
    stats = backend.stats()
    assert stats["request_count"] == 1
    assert stats["fallback_count"] == 1
    assert stats["fallback_reason_SolidLSPException"] == 1


def test_serena_golden_backend_normalizes_lsp_kind_and_line() -> None:
    """골든 심볼은 비교 일관성을 위해 kind/line을 정규화해야 한다."""

    class _DocSymbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "doWork",
                    "kind": 12,
                    "location": {
                        "relativePath": "src/App.vue",
                        "range": {
                            "start": {"line": 0},
                            "end": {"line": 1},
                        },
                    },
                }
            ]

    class _Lsp:
        def request_document_symbols(self, relative_path: str) -> _DocSymbols:
            del relative_path
            return _DocSymbols()

        def request_workspace_symbol(self, query: str) -> list[dict[str, object]]:
            del query
            return []

    class _Hub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.VUE

        def get_or_start(self, language: Language, repo_root: str) -> _Lsp:
            del language, repo_root
            return _Lsp()

    backend = SerenaGoldenBackend(hub=_Hub())  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="src/App.vue", content_hash="hash")
    assert result.error_message is None
    assert result.symbols == [
        {
            "name": "doWork",
            "kind": "function",
            "line": 1,
            "end_line": 2,
        }
    ]
