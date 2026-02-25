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
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.services.collection.service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO
from sari.services.pipeline.quality_service import (
    PipelineQualityService,
    SerenaGoldenBackend,
    _compute_symbol_counts,
)
from sari.services.workspace.service import WorkspaceService
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
    """품질 실행은 precision/error_rate와 position 지표를 포함한 요약을 반환해야 한다."""
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
    assert "position" in summary
    assert set(summary["position"].keys()) >= {"strict", "relaxed", "kind_match"}


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


def test_pipeline_quality_service_falls_back_to_repo_root_after_fanout_shape(tmp_path: Path) -> None:
    """fanout shape(모듈 row + 다른 scope_root)에서도 module repo_root 품질 실행이 가능해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    scope_root = str((tmp_path / "workspace").resolve())
    module_root = str((tmp_path / "workspace" / "mod-a").resolve())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                'r_mod', :repo_root, :scope_repo_root, 'a.py', :absolute_path, 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_root,
                "scope_repo_root": scope_root,
                "absolute_path": str((tmp_path / "workspace" / "mod-a" / "a.py").resolve()),
            },
        )
        conn.commit()

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )

    summary = quality_service.run(repo_root=module_root, limit_files=10, profile="default")
    assert summary["evaluated_files"] == 1


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


def test_pipeline_quality_service_uses_tool_layer_l3_symbols_as_predicted(tmp_path: Path) -> None:
    """L3 분리 저장소가 있으면 품질 predicted는 tool_data_l3_symbols를 우선 사용해야 한다."""
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

    file_repo = FileCollectionRepository(db_path)
    indexed_files = file_repo.list_files(repo_root=str(repo_dir.resolve()), limit=10)
    assert len(indexed_files) == 1
    file_item = indexed_files[0]

    # 기존 lsp_tool_data를 비워도 tool_data_l3_symbols만으로 품질 비교가 가능해야 한다.
    lsp_repo = LspToolDataRepository(db_path)
    lsp_repo.replace_symbols(
        str(repo_dir.resolve()),
        file_item.relative_path,
        file_item.content_hash,
        [],
        "2026-02-25T00:00:00Z",
    )

    tool_layer_repo = ToolDataLayerRepository(db_path)
    tool_layer_repo.upsert_l3_symbols(
        workspace_id=str(repo_dir.resolve()),
        repo_root=str(repo_dir.resolve()),
        relative_path=file_item.relative_path,
        content_hash=file_item.content_hash,
        symbols=[{"name": "alpha", "kind": "function", "line": 1, "end_line": 2}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at="2026-02-25T00:00:00Z",
    )

    quality_service = PipelineQualityService(
        file_repo=file_repo,
        lsp_repo=lsp_repo,
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
        tool_layer_repo=tool_layer_repo,
    )
    summary = quality_service.run(repo_root=str(repo_dir), limit_files=50, profile="default")
    assert summary["totals"]["symbol_tp"] >= 1
    assert summary["totals"]["symbol_fn"] == 0


def test_pipeline_quality_service_falls_back_to_latest_lsp_symbols_when_hash_mismatch(tmp_path: Path) -> None:
    """quality 경로는 strict hash miss 시 latest path 심볼로 폴백해야 한다."""
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

    # 현재 collected_files의 content_hash를 바꿔 strict hash miss를 의도적으로 만든다.
    file_path.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    collection_service.scan_once(str(repo_dir))

    quality_service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=PipelineQualityRepository(db_path),
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )
    summary = quality_service.run(repo_root=str(repo_dir), limit_files=50, profile="default")
    assert summary["totals"]["symbol_tp"] >= 1


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


def test_pipeline_quality_service_get_latest_report_filters_by_scope_repo_root(tmp_path: Path) -> None:
    """최신 리포트 조회는 전역 최신이 아니라 요청 repo scope 기준으로 선택해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    quality_repo = PipelineQualityRepository(db_path)

    repo_a = str((tmp_path / "repo-a").resolve())
    repo_b = str((tmp_path / "repo-b").resolve())

    run_a = quality_repo.create_run(
        repo_root=repo_a,
        scope_repo_root=repo_a,
        limit_files=10,
        profile="default",
        started_at="2026-02-25T10:00:00+00:00",
    )
    quality_repo.complete_run(
        run_id=run_a,
        finished_at="2026-02-25T10:00:10+00:00",
        status="PASSED",
        summary={"repo_root": repo_a, "status": "PASSED"},
    )
    run_b = quality_repo.create_run(
        repo_root=repo_b,
        scope_repo_root=repo_b,
        limit_files=10,
        profile="default",
        started_at="2026-02-25T10:01:00+00:00",
    )
    quality_repo.complete_run(
        run_id=run_b,
        finished_at="2026-02-25T10:01:10+00:00",
        status="FAILED",
        summary={"repo_root": repo_b, "status": "FAILED"},
    )

    service = PipelineQualityService(
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        quality_repo=quality_repo,
        golden_backend=_GoldenBackend(),
        artifact_root=tmp_path / "artifacts",
    )
    latest_a = service.get_latest_report(repo_root=repo_a)
    assert latest_a["repo_root"] == repo_a
    assert latest_a["status"] == "PASSED"


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


def test_compute_symbol_counts_matches_field_like_symbols_with_large_line_gap() -> None:
    """field/constant/enum_member는 라인 갭이 커도 이름/종류가 같으면 매칭되어야 한다."""
    tp, fp, fn = _compute_symbol_counts(
        predicted_symbols=[
            {"name": "updateDate", "kind": "field", "line": 241, "end_line": 241},
            {"name": "ACTIVE", "kind": "field", "line": 120, "end_line": 120},
            {"name": "log", "kind": "field", "line": 17, "end_line": 17},
        ],
        golden_symbols=[
            {"name": "updateDate", "kind": "field", "line": 22, "end_line": 22},
            {"name": "ACTIVE", "kind": "enum_member", "line": 22, "end_line": 22},
            {"name": "log", "kind": "constant", "line": 22, "end_line": 22},
        ],
        line_tolerance=2,
    )
    assert tp == 3
    assert fp == 0
    assert fn == 0
