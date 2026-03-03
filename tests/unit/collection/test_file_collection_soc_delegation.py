"""FileCollectionService 관심사 분리 위임 구조를 검증한다."""

from __future__ import annotations

import threading
import time
from pathlib import Path
import zlib

from sari.core.models import CollectedFileBodyDTO, CollectionPolicyDTO, CollectionScanResultDTO, PipelineMetricsDTO, WorkspaceDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from sari.services.collection.service import FileCollectionService
from sari.services.lsp_extraction_contracts import LspExtractionBackend, LspExtractionResultDTO
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


class _FanoutResolverStub:
    """fanout 대상 경로를 고정 반환하는 테스트 더블이다."""

    def __init__(self, targets: list[Path]) -> None:
        self._targets = targets

    def resolve_targets(self, root_path: Path) -> list[Path]:
        del root_path
        return self._targets


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


class _BlockingScannerStub:
    """watcher overflow rescan 비동기화를 검증하기 위한 블로킹 스캐너."""

    def __init__(self, *, release_wait_timeout_sec: float = 2.0) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []
        self._release_wait_timeout_sec = release_wait_timeout_sec

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        self.calls.append(repo_root)
        self.started.set()
        self.release.wait(timeout=self._release_wait_timeout_sec)
        return CollectionScanResultDTO(scanned_count=0, indexed_count=0, deleted_count=0)


class _L5UpgradeWatcherStub:
    """L5 watcher start/trigger_startup 호출을 기록하는 테스트 더블."""

    def __init__(self) -> None:
        self.started = 0
        self.triggered_repo_roots: list[str] = []

    def start(self) -> None:
        self.started += 1

    def trigger_startup(self, *, repo_root: str) -> None:
        self.triggered_repo_roots.append(repo_root)



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


def test_file_collection_service_enables_l5_admission_shadow_by_default_in_release_mode(tmp_path: Path) -> None:
    """release 모드 기본값에서는 L5 admission shadow가 켜져야 한다."""
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
        run_mode="release",
    )

    status = service.get_l5_admission_status()

    assert status["shadow_enabled"] is True
    assert status["enforced"] is False


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


def test_file_collection_service_watcher_overflow_rescan_is_async(tmp_path: Path) -> None:
    """watcher overflow rescan 예약은 watcher 루프를 블로킹하지 않아야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-overflow"
    repo_dir.mkdir()

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
    scanner_stub = _BlockingScannerStub()
    service._scanner = scanner_stub  # type: ignore[attr-defined]

    invoke_thread = threading.Thread(
        target=service._schedule_rescan_from_watcher,  # noqa: SLF001
        args=(str(repo_dir.resolve()),),
        daemon=True,
    )
    started_at = time.perf_counter()
    invoke_thread.start()
    invoke_thread.join(timeout=0.2)
    elapsed = time.perf_counter() - started_at

    assert invoke_thread.is_alive() is False
    assert elapsed < 0.2
    assert scanner_stub.started.wait(timeout=0.5) is True
    assert scanner_stub.calls == [str(repo_dir.resolve())]

    scanner_stub.release.set()
    service._stop_event.set()  # noqa: SLF001


def test_file_collection_service_watcher_overflow_rescan_uses_service_scan_entrypoint(tmp_path: Path) -> None:
    """overflow rescan은 scanner 직접호출이 아니라 service.scan_once 경로를 사용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-scan-entry"
    repo_dir.mkdir()
    repo_root = str(repo_dir.resolve())

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

    called = {"service": 0, "scanner": 0}

    def _service_scan_once(path: str) -> CollectionScanResultDTO:
        assert path == repo_root
        called["service"] += 1
        service._stop_event.set()  # noqa: SLF001
        return CollectionScanResultDTO(scanned_count=0, indexed_count=0, deleted_count=0)

    class _ScannerShouldNotBeUsed:
        def scan_once(self, path: str) -> CollectionScanResultDTO:
            _ = path
            called["scanner"] += 1
            service._stop_event.set()  # noqa: SLF001
            return CollectionScanResultDTO(scanned_count=0, indexed_count=0, deleted_count=0)

    service.scan_once = _service_scan_once  # type: ignore[method-assign]
    service._scanner = _ScannerShouldNotBeUsed()  # type: ignore[attr-defined]

    thread = threading.Thread(target=service._watcher_overflow_rescan_loop, daemon=True)  # noqa: SLF001
    service._watcher_rescan_queue.put_nowait(repo_root)  # noqa: SLF001
    thread.start()
    thread.join(timeout=1.0)

    assert called["service"] == 1
    assert called["scanner"] == 0


def test_file_collection_service_stop_background_waits_for_watcher_rescan_worker(tmp_path: Path) -> None:
    """장기 watcher rescan 중에는 stop_background가 worker 종료 전 반환하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-stop-race"
    repo_dir.mkdir()
    repo_root = str(repo_dir.resolve())

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
    scanner_stub = _BlockingScannerStub(release_wait_timeout_sec=10.0)
    service._scanner = scanner_stub  # type: ignore[attr-defined]

    service._runtime_manager.stop_background = lambda: service._stop_event.set()  # type: ignore[attr-defined]
    service._enrich_engine.shutdown = lambda: None  # type: ignore[attr-defined]
    service._repo_support.shutdown_probe_executor = lambda: None  # type: ignore[attr-defined]

    service._schedule_rescan_from_watcher(repo_root)  # noqa: SLF001
    assert scanner_stub.started.wait(timeout=0.5) is True
    with service._watcher_rescan_lock:  # noqa: SLF001
        assert repo_root in service._watcher_rescan_pending_roots  # noqa: SLF001

    stop_thread = threading.Thread(target=service.stop_background, daemon=True)
    stop_thread.start()

    # 기존 구현은 join(timeout=2.0) 이후 바로 반환하여 여기서 stop_thread가 종료된다.
    time.sleep(2.2)
    assert stop_thread.is_alive() is True
    with service._watcher_rescan_lock:  # noqa: SLF001
        assert repo_root in service._watcher_rescan_pending_roots  # noqa: SLF001

    scanner_stub.release.set()
    stop_thread.join(timeout=1.5)
    assert stop_thread.is_alive() is False


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


def test_file_collection_service_fanout_scan_marks_legacy_workspace_root_rows_deleted(tmp_path: Path) -> None:
    """fanout 스캔 시 workspace root legacy row는 stale 노출 방지를 위해 삭제 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = (tmp_path / "workspace").resolve()
    module_root = (workspace_root / "mod-a").resolve()
    module_root.mkdir(parents=True)

    class _CandidateSinkStub:
        def __init__(self) -> None:
            self.dirty_repo_roots: list[str] = []

        def mark_repo_dirty(self, repo_root: str) -> None:
            self.dirty_repo_roots.append(repo_root)

        def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
            del repo_root, relative_path

        def record_upsert(self, change) -> None:  # noqa: ANN001
            del change

        def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
            del repo_root, relative_path, reason

    candidate_sink = _CandidateSinkStub()

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
        candidate_index_sink=candidate_sink,
    )
    class _FanoutScanScannerStub:
        def scan_once(self, repo_root: str, scope_repo_root: str | None = None) -> CollectionScanResultDTO:
            del scope_repo_root
            assert repo_root == str(module_root)
            return CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    service._fanout_resolver = _FanoutResolverStub([module_root])  # type: ignore[attr-defined]
    service._scanner = _FanoutScanScannerStub()  # type: ignore[attr-defined]

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'sari/src/sari/services/file_collection_service.py', :absolute_path, '.',
                1, 1, 'legacy', 0, '2026-02-20T00:00:00+00:00', '2026-02-20T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(workspace_root),
                "scope_repo_root": str(workspace_root),
                "absolute_path": str((workspace_root / "sari/src/sari/services/file_collection_service.py").resolve()),
            },
        )
        conn.commit()

    _ = service.scan_once(str(workspace_root))

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT is_deleted, enrich_state
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND relative_path = 'sari/src/sari/services/file_collection_service.py'
            """,
            {"repo_root": str(workspace_root)},
        ).fetchone()
    assert row is not None
    assert int(row["is_deleted"]) == 1
    assert str(row["enrich_state"]) == "DELETED"
    assert candidate_sink.dirty_repo_roots == [str(workspace_root)]


def test_file_collection_service_fanout_scan_marks_stale_non_target_module_rows_deleted(tmp_path: Path) -> None:
    """fanout 대상에서 빠진 top-level 모듈 row는 stale 노출 방지를 위해 삭제 처리되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = (tmp_path / "workspace").resolve()
    module_root = (workspace_root / "src").resolve()
    stale_root = (workspace_root / "build").resolve()
    module_root.mkdir(parents=True)
    stale_root.mkdir(parents=True)

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

    class _FanoutScanScannerStub:
        def scan_once(self, repo_root: str, scope_repo_root: str | None = None) -> CollectionScanResultDTO:
            del scope_repo_root
            assert repo_root == str(module_root)
            return CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    service._fanout_resolver = _FanoutResolverStub([module_root])  # type: ignore[attr-defined]
    service._scanner = _FanoutScanScannerStub()  # type: ignore[attr-defined]

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'X.java', :absolute_path, '.',
                1, 1, 'legacy-build', 0, '2026-02-20T00:00:00+00:00', '2026-02-20T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(stale_root),
                "scope_repo_root": str(workspace_root),
                "absolute_path": str((stale_root / "X.java").resolve()),
            },
        )
        conn.commit()

    _ = service.scan_once(str(workspace_root))

    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT is_deleted, enrich_state
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND relative_path = 'X.java'
            """,
            {"repo_root": str(stale_root)},
        ).fetchone()
    assert row is not None
    assert int(row["is_deleted"]) == 1
    assert str(row["enrich_state"]) == "DELETED"


def test_file_collection_service_fanout_scan_does_not_delete_independent_child_scope_rows(tmp_path: Path) -> None:
    """fanout cleanup은 부모 scope 산출물만 정리하고 독립 child scope 데이터는 유지해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = (tmp_path / "workspace").resolve()
    module_root = (workspace_root / "src").resolve()
    child_root = (workspace_root / "build").resolve()
    module_root.mkdir(parents=True)
    child_root.mkdir(parents=True)

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

    class _FanoutScanScannerStub:
        def scan_once(self, repo_root: str, scope_repo_root: str | None = None) -> CollectionScanResultDTO:
            del scope_repo_root
            assert repo_root == str(module_root)
            return CollectionScanResultDTO(scanned_count=1, indexed_count=1, deleted_count=0)

    service._fanout_resolver = _FanoutResolverStub([module_root])  # type: ignore[attr-defined]
    service._scanner = _FanoutScanScannerStub()  # type: ignore[attr-defined]

    with connect(db_path) as conn:
        # parent workspace fanout 산출물처럼 보이는 stale 행
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'stale.java', :absolute_path, '.',
                1, 1, 'stale', 0, '2026-02-20T00:00:00+00:00', '2026-02-20T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(child_root),
                "scope_repo_root": str(workspace_root),
                "absolute_path": str((child_root / "stale.java").resolve()),
            },
        )
        # child repo 자체 scope(독립 등록)로 유지되어야 하는 행
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, 'owned.java', :absolute_path, '.',
                1, 1, 'owned', 0, '2026-02-20T00:00:00+00:00', '2026-02-20T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(child_root),
                "scope_repo_root": str(child_root),
                "absolute_path": str((child_root / "owned.java").resolve()),
            },
        )
        conn.commit()

    _ = service.scan_once(str(workspace_root))

    with connect(db_path) as conn:
        stale_row = conn.execute(
            """
            SELECT is_deleted, enrich_state
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND scope_repo_root = :scope_repo_root
              AND relative_path = 'stale.java'
            """,
            {"repo_root": str(child_root), "scope_repo_root": str(workspace_root)},
        ).fetchone()
        owned_row = conn.execute(
            """
            SELECT is_deleted, enrich_state
            FROM collected_files_l1
            WHERE repo_root = :repo_root
              AND scope_repo_root = :scope_repo_root
              AND relative_path = 'owned.java'
            """,
            {"repo_root": str(child_root), "scope_repo_root": str(child_root)},
        ).fetchone()

    assert stale_row is not None
    assert int(stale_row["is_deleted"]) == 1
    assert str(stale_row["enrich_state"]) == "DELETED"
    assert owned_row is not None
    assert int(owned_row["is_deleted"]) == 0


def test_file_collection_service_start_background_triggers_l5_startup_reconciliation_for_active_workspaces(
    tmp_path: Path,
) -> None:
    """start_background()는 활성 workspace에 대해 L5 startup reconciliation을 호출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    active_root = str((tmp_path / "repo-active").resolve())
    inactive_root = str((tmp_path / "repo-inactive").resolve())
    workspace_repo.add(WorkspaceDTO(path=active_root, name="active", indexed_at=None, is_active=True))
    workspace_repo.add(WorkspaceDTO(path=inactive_root, name="inactive", indexed_at=None, is_active=False))

    service = FileCollectionService(
        workspace_repo=workspace_repo,
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
    l5_stub = _L5UpgradeWatcherStub()
    service._l5_upgrade_watcher = l5_stub  # type: ignore[attr-defined]
    service._runtime_manager.start_background = lambda: None  # type: ignore[attr-defined]
    service._ensure_watcher_rescan_worker_started = lambda: None  # type: ignore[attr-defined]

    service.start_background()

    assert l5_stub.started == 1
    assert l5_stub.triggered_repo_roots == [active_root]
