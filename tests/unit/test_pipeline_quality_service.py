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
from sari.services.pipeline_quality_service import (
    PipelineQualityService,
    SerenaGoldenBackend,
    _compute_symbol_counts,
)
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
    assert summary["recall"]["total"] >= 95.0
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


def test_pipeline_quality_service_fails_when_recall_is_low(tmp_path: Path) -> None:
    """recall이 임계값보다 낮으면 게이트 실패여야 한다."""

    class _SparseGoldenBackend(LspExtractionBackend):
        def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
            del repo_root, relative_path, content_hash
            # predicted(1 symbol) 대비 golden(3 symbols)으로 recall을 의도적으로 낮춘다.
            return LspExtractionResultDTO(
                symbols=[
                    {"name": "alpha", "kind": "function", "line": 1, "end_line": 2},
                    {"name": "beta", "kind": "function", "line": 5, "end_line": 7},
                    {"name": "gamma", "kind": "function", "line": 9, "end_line": 10},
                ],
                relations=[],
                error_message=None,
            )

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
        golden_backend=_SparseGoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )
    summary = quality_service.run(repo_root=str(repo_dir), limit_files=50, profile="default")

    assert summary["recall"]["total"] < 95.0
    assert summary["status"] == "FAILED"


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


def test_pipeline_quality_service_excludes_benchmark_dataset_paths(tmp_path: Path) -> None:
    """품질 실행은 benchmark_dataset 경로를 평가 대상에서 제외해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    benchmark_dir = repo_dir / "benchmark_dataset"
    benchmark_dir.mkdir()
    (repo_dir / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (benchmark_dir / "sample.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
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

    assert summary["evaluated_files"] == 1
    assert summary["error_files"] == 0
    samples = summary.get("samples", [])
    assert isinstance(samples, list)
    assert all("benchmark_dataset/" not in str(item.get("relative_path", "")) for item in samples)


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


def test_serena_golden_backend_normalizes_line_from_range_without_location() -> None:
    """documentSymbol이 location 없이 range만 줄 때도 line을 정규화해야 한다."""

    class _DocSymbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "alpha",
                    "kind": 12,
                    "range": {
                        "start": {"line": 10},
                        "end": {"line": 14},
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
            return Language.TYPESCRIPT

        def get_or_start(self, language: Language, repo_root: str) -> _Lsp:
            del language, repo_root
            return _Lsp()

    backend = SerenaGoldenBackend(hub=_Hub())  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="src/app.js", content_hash="hash")
    assert result.error_message is None
    assert result.symbols == [
        {
            "name": "alpha",
            "kind": "function",
            "line": 11,
            "end_line": 15,
        }
    ]


def test_compute_symbol_counts_matches_with_line_tolerance() -> None:
    """심볼 TP 계산은 end_line이 달라도 line tolerance 내에서 매칭되어야 한다."""
    tp, fp, fn = _compute_symbol_counts(
        predicted_symbols=[
            {"name": "makeQueryOptionPaging", "kind": "function", "line": 21, "end_line": 21},
            {"name": "alpha", "kind": "class", "line": 10, "end_line": 10},
        ],
        golden_symbols=[
            {"name": "makeQueryOptionPaging", "kind": "function", "line": 22, "end_line": 35},
            {"name": "beta", "kind": "class", "line": 99, "end_line": 120},
        ],
        line_tolerance=2,
    )
    assert tp == 1
    assert fp == 1
    assert fn == 1
