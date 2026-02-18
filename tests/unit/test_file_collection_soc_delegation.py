"""FileCollectionService 관심사 분리 위임 구조를 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import CollectionPolicyDTO, CollectionScanResultDTO, PipelineMetricsDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.services.file_collection_service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO


class _NoopLspBackend(LspExtractionBackend):
    """LSP 추출을 빈 결과로 처리하는 테스트 더블이다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


class _ScannerStub:
    """스캐너 위임 호출을 검증하기 위한 테스트 더블이다."""

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        """고정 스캔 결과를 반환한다."""
        assert repo_root.endswith("repo-stub")
        return CollectionScanResultDTO(scanned_count=11, indexed_count=7, deleted_count=3)


class _WorkerStub:
    """파이프라인 워커 위임 호출을 검증하기 위한 테스트 더블이다."""

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """고정 처리 건수를 반환한다."""
        assert limit == 13
        return 5


class _WatcherStub:
    """watcher 위임 호출을 검증하기 위한 테스트 더블이다."""

    def __init__(self) -> None:
        self.called: list[tuple[str, str, str]] = []

    def handle_fs_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """이벤트 전달 파라미터를 기록한다."""
        self.called.append((event_type, src_path, dest_path))


class _MetricsStub:
    """메트릭 위임 호출을 검증하기 위한 테스트 더블이다."""

    def __init__(self) -> None:
        self.get_called = 0
        self.recorded: list[float] = []

    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        """고정 메트릭 DTO를 반환한다."""
        self.get_called += 1
        return PipelineMetricsDTO(
            queue_depth=0,
            running_jobs=0,
            failed_jobs=0,
            dead_jobs=0,
            done_jobs=0,
            avg_enrich_latency_ms=0.0,
            indexing_mode="steady",
        )

    def record_enrich_latency(self, latency_ms: float) -> None:
        """지연시간 기록 파라미터를 보관한다."""
        self.recorded.append(latency_ms)



def _policy() -> CollectionPolicyDTO:
    """테스트 기본 수집 정책을 반환한다."""
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


def test_file_collection_service_delegates_to_scanner_and_worker(tmp_path: Path) -> None:
    """scan/l2 처리 호출은 전용 컴포넌트로 위임되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )

    service._scanner = _ScannerStub()  # type: ignore[attr-defined]
    service._pipeline_worker = _WorkerStub()  # type: ignore[attr-defined]

    result = service.scan_once("repo-stub")
    processed = service.process_enrich_jobs_l2(limit=13)

    assert result.scanned_count == 11
    assert result.indexed_count == 7
    assert result.deleted_count == 3
    assert processed == 5


def test_file_collection_service_delegates_watcher_and_metrics(tmp_path: Path) -> None:
    """watcher/metrics 관련 호출도 전용 컴포넌트로 위임되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )

    watcher_stub = _WatcherStub()
    metrics_stub = _MetricsStub()
    service._watcher = watcher_stub  # type: ignore[attr-defined]
    service._metrics_service = metrics_stub  # type: ignore[attr-defined]

    service._handle_fs_event(event_type="modified", src_path="/tmp/a.py", dest_path="")
    metrics = service.get_pipeline_metrics()
    service._record_enrich_latency(12.5)

    assert watcher_stub.called == [("modified", "/tmp/a.py", "")]
    assert metrics_stub.get_called == 1
    assert metrics_stub.recorded == [12.5]
    assert metrics.indexing_mode == "steady"


def test_file_collection_service_watcher_path_skips_non_collectible_files(tmp_path: Path) -> None:
    """watcher 경유 인덱싱은 정책 비대상 파일을 큐에 적재하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "FETCH_HEAD").write_text("dummy", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
    )

    service._index_file_with_priority(  # noqa: SLF001
        repo_root=str(repo_dir.resolve()),
        relative_path=".git/FETCH_HEAD",
        priority=90,
        enqueue_source="watcher",
    )

    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM file_enrich_queue").fetchone()
        assert row is not None
        assert int(row["cnt"]) == 0
