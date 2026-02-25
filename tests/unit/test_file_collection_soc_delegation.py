"""FileCollectionService 관심사 분리 위임 구조를 검증한다."""

from __future__ import annotations

from pathlib import Path
import zlib

from sari.core.models import CollectedFileBodyDTO, CollectionPolicyDTO, CollectionScanResultDTO, PipelineMetricsDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.services.file_collection_service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO
from solidlsp.ls_config import Language


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


def test_file_collection_service_watcher_signal_updates_hotness_and_broker_can_grant_hot_lease(tmp_path: Path) -> None:
    """watcher cheap signal -> hotness tracker -> broker hot lane baseline 흐름을 검증한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-hot"
    (repo_dir / "app").mkdir(parents=True)
    file_path = repo_dir / "app" / "main.ts"
    file_path.write_text("export const x = 1;\n", encoding="utf-8")

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
        lsp_session_broker_enabled=True,
    )

    service._on_watcher_signal(  # noqa: SLF001
        event_type="modified",
        repo_root=str(repo_dir.resolve()),
        relative_path="app/main.ts",
        dest_path="",
    )

    scope_hint = service._derive_hotness_scope_hint(  # noqa: SLF001
        repo_root=str(repo_dir.resolve()),
        relative_path="app/main.ts",
    )
    assert isinstance(scope_hint, str)
    hotness = service._watcher_hotness_tracker.get_scope_hotness(  # noqa: SLF001
        language=Language.TYPESCRIPT,
        lsp_scope_root=scope_hint,
    )
    assert hotness > 0.0

    lease = service._lsp_session_broker.acquire_lease(  # noqa: SLF001
        language=Language.TYPESCRIPT,
        lsp_scope_root=scope_hint,
        lane="hot",
        hotness_score=hotness,
        pending_jobs_in_scope=3,
    )
    assert lease.granted is True
    assert lease.lane == "hot"
    service._lsp_session_broker.release_lease(lease)  # noqa: SLF001


def test_file_collection_service_list_files_falls_back_to_repo_root_after_fanout_shape(tmp_path: Path) -> None:
    """fanout shape(모듈 row + 다른 scope_root)에서도 module repo list_files가 비지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    scope_root = str((tmp_path / "workspace").resolve())
    module_root = str((tmp_path / "workspace" / "mod-a").resolve())

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

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                'r_mod', :repo_root, :scope_repo_root, 'alpha.py', :absolute_path, 'mod-a',
                1, 10, 'h1', 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": module_root,
                "scope_repo_root": scope_root,
                "absolute_path": str((tmp_path / "workspace" / "mod-a" / "alpha.py").resolve()),
            },
        )
        conn.commit()

    rows = service.list_files(repo_root=module_root, limit=10, prefix=None)
    assert len(rows) == 1
    assert rows[0]["relative_path"] == "alpha.py"


def test_file_collection_service_read_file_falls_back_to_repo_root_after_fanout_shape(tmp_path: Path) -> None:
    """fanout shape에서도 module repo_root read_file이 metadata/body를 정상 조회해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    scope_root = str((tmp_path / "workspace").resolve())
    module_root = str((tmp_path / "workspace" / "mod-a").resolve())
    absolute_path = str((tmp_path / "workspace" / "mod-a" / "alpha.py").resolve())
    content = "line1\nline2\n"
    content_hash = "h1"
    now_iso = "2026-02-25T00:00:00+00:00"

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

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                'r_mod', :repo_root, :scope_repo_root, 'alpha.py', :absolute_path, 'mod-a',
                1, 10, :content_hash, 0, :now_iso, :now_iso, 'DONE'
            )
            """,
            {
                "repo_root": module_root,
                "scope_repo_root": scope_root,
                "absolute_path": absolute_path,
                "content_hash": content_hash,
                "now_iso": now_iso,
            },
        )
        conn.commit()

    service._body_repo.upsert_body(  # noqa: SLF001
        CollectedFileBodyDTO(
            repo_id="r_mod",
            repo_root=module_root,
            scope_repo_root=scope_root,
            relative_path="alpha.py",
            content_hash=content_hash,
            content_zlib=zlib.compress(content.encode("utf-8")),
            content_len=len(content.encode("utf-8")),
            normalized_text=content,
            created_at=now_iso,
            updated_at=now_iso,
        )
    )

    result = service.read_file(repo_root=module_root, relative_path="alpha.py", offset=0, limit=None)
    assert result.source == "l2"
    assert result.content == content.rstrip("\n")
