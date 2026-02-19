"""Batch-17 성능/자원관리 하드닝 요구사항을 검증한다."""

from __future__ import annotations

from pathlib import Path

import hashlib

from sari.core.models import CandidateIndexChangeDTO, CollectedFileL1DTO, CollectionPolicyDTO, FileEnrichJobDTO, WorkspaceDTO
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect, init_schema
from solidlsp.ls_config import Language
from sari.search.candidate_search import CandidateBackendError, CandidateSearchConfig, CandidateSearchResultDTO, TantivyCandidateBackend
from sari.search.orchestrator import SearchOrchestrator
from sari.services.file_collection_service import FileCollectionService, LspExtractionBackend, LspExtractionResultDTO, SolidLspExtractionBackend


class _NoopLspBackend(LspExtractionBackend):
    """테스트용 no-op LSP 추출 백엔드다."""

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        """항상 빈 LSP 결과를 반환한다."""
        del repo_root, relative_path, content_hash
        return LspExtractionResultDTO(symbols=[], relations=[], error_message=None)


class _CaptureHotLanguageBackend(_NoopLspBackend):
    """prewarm 상위 언어 설정을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.repo_root: str | None = None
        self.languages: set[Language] = set()

    def configure_hot_languages(self, repo_root: str, languages: set[Language]) -> None:
        """설정된 저장소/언어 목록을 보관한다."""
        self.repo_root = repo_root
        self.languages = set(languages)


class _DirtySink:
    """후보 인덱스 dirty 호출을 추적하는 테스트 더블이다."""

    def __init__(self) -> None:
        """호출 카운터를 초기화한다."""
        self.repo_calls: int = 0
        self.file_calls: int = 0
        self.upsert_calls: int = 0
        self.delete_calls: int = 0

    def mark_repo_dirty(self, repo_root: str) -> None:
        """저장소 단위 dirty 호출 횟수를 증가시킨다."""
        _ = repo_root
        self.repo_calls += 1

    def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
        """파일 단위 dirty 호출 횟수를 증가시킨다."""
        _ = (repo_root, relative_path)
        self.file_calls += 1

    def record_upsert(self, change: CandidateIndexChangeDTO) -> None:
        """파일 upsert 이벤트 호출 횟수를 증가시킨다."""
        _ = change
        self.upsert_calls += 1

    def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
        """파일 delete 이벤트 호출 횟수를 증가시킨다."""
        _ = (repo_root, relative_path, reason)
        self.delete_calls += 1


class _CaptureVectorSink:
    """벡터 임베딩 upsert 호출을 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        """호출 인자를 기록한다."""
        self.calls.append((repo_root, relative_path, content_hash, content_text))


class _FailingVectorSink:
    """벡터 임베딩 업서트 실패를 발생시키는 테스트 더블이다."""

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        """항상 명시적 런타임 오류를 발생시킨다."""
        del repo_root, relative_path, content_hash, content_text
        raise RuntimeError("vector write failed")


class _CaptureCandidateService:
    """오케스트레이터 입력 워크스페이스를 캡처하는 테스트 더블이다."""

    def __init__(self) -> None:
        """초기 캡처 상태를 준비한다."""
        self.last_workspaces: list[WorkspaceDTO] = []

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> CandidateSearchResultDTO:
        """입력 워크스페이스를 저장하고 빈 결과를 반환한다."""
        del query, limit
        self.last_workspaces = list(workspaces)
        return CandidateSearchResultDTO(candidates=[], source="scan", errors=[])

    def filter_workspaces_by_repo(self, workspaces: list[WorkspaceDTO], repo_root: str) -> list[WorkspaceDTO]:
        """repo 필터 정책을 실제 서비스와 동일하게 모사한다."""
        return [workspace for workspace in workspaces if workspace.path == repo_root]


class _NoopSymbolService:
    """빈 해석 결과를 반환하는 테스트 더블이다."""

    def resolve(self, candidates: list[object], query: str, limit: int) -> tuple[list[object], list[object]]:
        """빈 결과를 반환한다."""
        del candidates, query, limit
        return [], []


def test_solid_lsp_extraction_backend_accepts_document_symbol_without_relative_path() -> None:
    """documentSymbol에 relativePath가 없어도 심볼을 추출해야 한다."""

    class _Symbols:
        def iter_symbols(self) -> list[dict[str, object]]:
            return [
                {
                    "name": "alpha",
                    "kind": "function",
                    "location": {
                        "range": {
                            "start": {"line": 3},
                            "end": {"line": 8},
                        }
                    },
                }
            ]

    class _FakeLsp:
        def request_document_symbols(self, relative_path: str) -> _Symbols:
            del relative_path
            return _Symbols()

    class _FakeHub:
        def resolve_language(self, relative_path: str) -> Language:
            del relative_path
            return Language.PYTHON

        def get_or_start(self, language: Language, repo_root: str) -> _FakeLsp:
            del language, repo_root
            return _FakeLsp()

        def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
            del language, repo_root

    backend = SolidLspExtractionBackend(hub=_FakeHub())  # type: ignore[arg-type]
    result = backend.extract(repo_root="/repo", relative_path="a.py", content_hash="h")
    assert result.error_message is None
    assert len(result.symbols) == 1
    assert result.symbols[0]["name"] == "alpha"
    assert isinstance(result.symbols[0]["symbol_key"], str)
    assert result.symbols[0]["parent_symbol_key"] is None
    assert int(result.symbols[0]["depth"]) == 0


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


def test_file_collection_scan_once_skips_unchanged_read_bytes(tmp_path: Path, monkeypatch) -> None:
    """동일 파일 재스캔 시 본문 재읽기를 건너뛰어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "a.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")

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
    service.scan_once(str(repo_dir.resolve()))

    original_read_bytes = Path.read_bytes
    read_count = {"value": 0}

    def _tracked_read_bytes(path: Path) -> bytes:
        if path == target:
            read_count["value"] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _tracked_read_bytes)

    service.scan_once(str(repo_dir.resolve()))

    assert read_count["value"] == 0


def test_vector_embedding_runs_even_when_body_persistence_disabled(tmp_path: Path) -> None:
    """L2 본문 저장 비활성화 상태에서도 벡터 임베딩은 독립적으로 실행되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-vector"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    vector_sink = _CaptureVectorSink()
    body_repo = FileBodyRepository(db_path)

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=body_repo,
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        policy_repo=None,
        event_repo=None,
        vector_index_sink=vector_sink,
        persist_body_for_read=False,
    )
    service.scan_once(str(repo_dir.resolve()))
    processed = service.process_enrich_jobs(limit=50)

    assert processed >= 1
    assert len(vector_sink.calls) >= 1
    # L2 본문 저장은 비활성화되어야 한다.
    first = vector_sink.calls[0]
    assert body_repo.read_body_text(first[0], first[1], first[2]) is None


def test_tantivy_sync_index_skips_unchanged_file_read(tmp_path: Path, monkeypatch) -> None:
    """Tantivy 동기화는 변경 없는 파일을 다시 읽지 않아야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    original_read_bytes = Path.read_bytes
    read_count = {"value": 0}

    def _tracked_read_bytes(path: Path) -> bytes:
        if path == target:
            read_count["value"] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _tracked_read_bytes)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert read_count["value"] == 0


def test_tantivy_search_accepts_special_chars_query(tmp_path: Path) -> None:
    """특수문자 포함 질의도 파싱 실패 없이 처리되어야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("if (x > 0):\n    return x\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-special",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    items = backend.search(workspaces=[workspace], query="if (x > 0):", limit=10)

    assert len(items) >= 1


def test_tantivy_search_does_not_sync_every_request(tmp_path: Path, monkeypatch) -> None:
    """인덱스가 clean 상태이면 검색마다 전체 sync를 반복하지 않아야 한다."""
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-clean",
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True)

    sync_count = {"value": 0}
    original_sync_index = backend._sync_index

    def _tracked_sync(workspaces: list[WorkspaceDTO]) -> None:
        sync_count["value"] += 1
        original_sync_index(workspaces)

    monkeypatch.setattr(backend, "_sync_index", _tracked_sync)

    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert sync_count["value"] == 1


def test_search_orchestrator_prefilters_repo_before_candidate_search() -> None:
    """repo 지정 시 후보 검색 전 단계에서 워크스페이스가 축소되어야 한다."""

    class _WorkspaceRepo:
        """고정 워크스페이스 목록 저장소 더블이다."""

        def list_all(self) -> list[WorkspaceDTO]:
            """고정 워크스페이스 목록을 반환한다."""
            return [
                WorkspaceDTO(path="/repo-a", name="repo-a", indexed_at=None, is_active=True),
                WorkspaceDTO(path="/repo-b", name="repo-b", indexed_at=None, is_active=True),
            ]

    candidate = _CaptureCandidateService()
    orchestrator = SearchOrchestrator(
        workspace_repo=_WorkspaceRepo(),
        candidate_service=candidate,
        symbol_service=_NoopSymbolService(),
    )

    orchestrator.search(query="hello", limit=10, repo_root="/repo-a")

    assert len(candidate.last_workspaces) == 1
    assert candidate.last_workspaces[0].path == "/repo-a"


def test_sqlite_connect_works_after_wal_initialized(tmp_path: Path) -> None:
    """초기화 이후 일반 연결은 안정적으로 동작해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)

    with connect(db_path) as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()

    assert row is not None
    assert str(row[0]).lower() == "wal"


def test_file_collection_scan_once_does_not_delete_file_seen_after_scan_start(tmp_path: Path) -> None:
    """스캔 도중 최근에 관측된 파일은 삭제 처리되면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    file_repo = FileCollectionRepository(db_path)
    now = "2026-02-17T00:00:00+00:00"
    file_repo.upsert_file(
        CollectedFileL1DTO(
            repo_id="r_repo",
            repo_root="/repo",
            relative_path="new.py",
            absolute_path="/repo/new.py",
            repo_label="repo",
            mtime_ns=1,
            size_bytes=1,
            content_hash="h",
            is_deleted=False,
            last_seen_at="2026-02-17T00:10:00+00:00",
            updated_at=now,
            enrich_state="PENDING",
        )
    )

    deleted = file_repo.mark_missing_as_deleted(
        repo_root="/repo",
        seen_relative_paths=[],
        updated_at=now,
        scan_started_at="2026-02-17T00:05:00+00:00",
    )
    row = file_repo.get_file(repo_root="/repo", relative_path="new.py")
    assert deleted == 0
    assert row is not None
    assert row.is_deleted is False


def test_vector_embedding_failure_marks_job_failed(tmp_path: Path) -> None:
    """벡터 임베딩 실패는 실패 상태로 승격되어 재시도 대상이 되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-vector-fail"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        vector_index_sink=_FailingVectorSink(),
    )
    service.scan_once(str(repo_dir.resolve()))
    processed = service.process_enrich_jobs_l2(limit=50)

    assert processed >= 1
    state = FileCollectionRepository(db_path).get_file(str(repo_dir.resolve()), "a.py")
    assert state is not None
    assert state.enrich_state == "FAILED"


def test_file_collection_scan_once_marks_candidate_index_dirty(tmp_path: Path) -> None:
    """scan_once에서 변경이 발생하면 후보 인덱스 dirty를 호출해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-b"
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    dirty_sink = _DirtySink()

    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=_policy(),
        lsp_backend=_NoopLspBackend(),
        candidate_index_sink=dirty_sink,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert dirty_sink.upsert_calls == 1


def test_file_collection_scan_once_configures_top_hot_languages(tmp_path: Path) -> None:
    """scan 결과 기준 상위 언어만 prewarm 대상으로 설정해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-hot"
    repo_dir.mkdir()

    for index in range(40):
        (repo_dir / f"py_{index}.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    for index in range(35):
        (repo_dir / f"kt_{index}.kt").write_text("fun alpha(): Int = 1\n", encoding="utf-8")
    for index in range(20):
        (repo_dir / f"go_{index}.go").write_text("package main\nfunc alpha() int { return 1 }\n", encoding="utf-8")

    backend = _CaptureHotLanguageBackend()
    policy = CollectionPolicyDTO(
        include_ext=(".py", ".kt", ".go"),
        exclude_globs=("**/.git/**",),
        max_file_size_bytes=512 * 1024,
        scan_interval_sec=120,
        max_enrich_batch=100,
        retry_max_attempts=2,
        retry_backoff_base_sec=1,
        queue_poll_interval_ms=100,
    )
    service = FileCollectionService(
        workspace_repo=WorkspaceRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        readiness_repo=ToolReadinessRepository(db_path),
        policy=policy,
        lsp_backend=backend,
    )

    service.scan_once(str(repo_dir.resolve()))

    assert backend.repo_root == str(repo_dir.resolve())
    assert backend.languages == {Language.PYTHON, Language.KOTLIN}


def test_file_collection_rebalance_jobs_by_language_round_robin(tmp_path: Path) -> None:
    """언어 버킷을 라운드로빈으로 교차 배치해야 한다."""
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
    )

    jobs = [
        FileEnrichJobDTO(job_id="j1", repo_id="r_r", repo_root="/r", relative_path="a.py", content_hash="h1", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j2", repo_id="r_r", repo_root="/r", relative_path="b.py", content_hash="h2", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j3", repo_id="r_r", repo_root="/r", relative_path="c.kt", content_hash="h3", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
        FileEnrichJobDTO(job_id="j4", repo_id="r_r", repo_root="/r", relative_path="d.kt", content_hash="h4", priority=90, enqueue_source="scan", status="RUNNING", attempt_count=0, last_error=None, next_retry_at="t", created_at="t", updated_at="t"),
    ]

    rebalanced = service._rebalance_jobs_by_language(jobs)
    ordered_paths = [job.relative_path for job in rebalanced]

    assert ordered_paths == ["a.py", "c.kt", "b.py", "d.kt"]


def test_tantivy_applies_pending_change_without_full_sync(tmp_path: Path, monkeypatch) -> None:
    """pending change가 있으면 전체 sync 없이 인덱스에 반영되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-c"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_c",
            repo_root=str(repo_dir.resolve()),
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-change",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-c", indexed_at=None, is_active=True)
    sync_count = {"value": 0}
    original_sync_index = backend._sync_index

    def _tracked_sync(workspaces: list[WorkspaceDTO]) -> None:
        sync_count["value"] += 1
        original_sync_index(workspaces)

    monkeypatch.setattr(backend, "_sync_index", _tracked_sync)
    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert len(items) == 1
    assert sync_count["value"] == 0


def test_tantivy_applies_delete_change_and_removes_document(tmp_path: Path) -> None:
    """delete change 반영 실패가 감지되면 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-d"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_d",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-delete",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-d", indexed_at=None, is_active=True)
    indexed = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    assert len(indexed) == 1

    change_repo.enqueue_delete(
        repo_id="r_repo_d",
        repo_root=repo_root,
        relative_path="alpha.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )
    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate delete visibility check failed" in str(exc)


def test_tantivy_backend_does_not_use_tombstone_filter_field(tmp_path: Path) -> None:
    """Batch-22 이후 백엔드는 tombstone 필드에 의존하지 않아야 한다."""
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-no-tombstone",
    )
    assert not hasattr(backend, "_suppressed_paths")
    assert not hasattr(backend, "_deleted_paths_cache")
    assert hasattr(backend, "_writer")


def test_tantivy_pending_change_failure_raises_backend_error(tmp_path: Path) -> None:
    """pending 변경 적용 실패는 즉시 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-e"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    # mtime/size를 의도적으로 잘못 넣어 apply 실패를 유도한다.
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_e",
            repo_root=str(repo_dir.resolve()),
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=1,
            size_bytes=2,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-fail",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-e", indexed_at=None, is_active=True)

    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate apply failed" in str(exc)


def test_tantivy_delete_visibility_failure_escalates_backend_error(tmp_path: Path, monkeypatch) -> None:
    """삭제 후 가시성 검증 실패는 즉시 backend error로 승격되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-f"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_f",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-delete-visibility-fail",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-f", indexed_at=None, is_active=True)
    _ = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    change_repo.enqueue_delete(
        repo_id="r_repo_f",
        repo_root=repo_root,
        relative_path="alpha.py",
        event_source="watcher",
        recorded_at="2026-02-16T00:00:01+00:00",
    )

    def _always_visible(repo_root: str, relative_path: str) -> bool:
        _ = (repo_root, relative_path)
        return True

    monkeypatch.setattr(backend, "_is_repo_path_visible", _always_visible)

    try:
        backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
        assert False, "expected CandidateBackendError"
    except CandidateBackendError as exc:
        assert "candidate delete visibility check failed" in str(exc)


def test_tantivy_apply_allows_repo_under_active_workspace(tmp_path: Path) -> None:
    """active workspace 하위 repo는 candidate apply에서 비활성으로 오판하면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_root = tmp_path / "workspace"
    repo_dir = workspace_root / "apps" / "repo-g"
    repo_dir.mkdir(parents=True)
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()

    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_g",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-16T00:00:00+00:00",
        )
    )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-workspace-child",
        change_repo=change_repo,
    )
    workspace = WorkspaceDTO(path=str(workspace_root.resolve()), name="workspace", indexed_at=None, is_active=True)
    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)

    assert len(items) == 1
    assert items[0].relative_path == "alpha.py"


def test_tantivy_pending_apply_respects_batch_cap(tmp_path: Path) -> None:
    """검색 1회당 pending apply는 설정된 배치 상한을 넘기면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-batch-cap"
    repo_dir.mkdir()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    workspace = WorkspaceDTO(path=repo_root, name="repo-batch-cap", indexed_at=None, is_active=True)

    for index in range(15):
        target = repo_dir / f"alpha_{index}.py"
        target.write_text(f"def alpha_symbol_{index}():\n    return {index}\n", encoding="utf-8")
        raw = target.read_bytes()
        change_repo.enqueue_upsert(
            CandidateIndexChangeDTO(
                repo_id="r_repo_batch_cap",
                repo_root=repo_root,
                relative_path=f"alpha_{index}.py",
                absolute_path=str(target.resolve()),
                content_hash=hashlib.sha256(raw).hexdigest(),
                mtime_ns=target.stat().st_mtime_ns,
                size_bytes=target.stat().st_size,
                event_source="scan",
                recorded_at="2026-02-19T00:00:00+00:00",
            )
        )

    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-batch-cap",
        change_repo=change_repo,
        max_pending_apply_per_search=5,
        max_maintenance_ms_per_search=1000,
    )

    first_items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=50)
    assert len(first_items) == 5
    assert len(change_repo.acquire_pending(limit=100)) == 10

    second_items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=50)
    assert len(second_items) == 10


def test_tantivy_pending_apply_respects_zero_time_budget(tmp_path: Path) -> None:
    """maintenance 시간 예산이 0이면 pending apply는 이월되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-budget-zero"
    repo_dir.mkdir()
    target = repo_dir / "alpha.py"
    target.write_text("def alpha_symbol():\n    return 1\n", encoding="utf-8")
    raw = target.read_bytes()
    change_repo = CandidateIndexChangeRepository(db_path)
    repo_root = str(repo_dir.resolve())
    change_repo.enqueue_upsert(
        CandidateIndexChangeDTO(
            repo_id="r_repo_budget_zero",
            repo_root=repo_root,
            relative_path="alpha.py",
            absolute_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            mtime_ns=target.stat().st_mtime_ns,
            size_bytes=target.stat().st_size,
            event_source="scan",
            recorded_at="2026-02-19T00:00:00+00:00",
        )
    )
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-budget-zero",
        change_repo=change_repo,
        max_pending_apply_per_search=10,
        max_maintenance_ms_per_search=0,
    )
    workspace = WorkspaceDTO(path=repo_root, name="repo-budget-zero", indexed_at=None, is_active=True)

    items = backend.search(workspaces=[workspace], query="alpha_symbol", limit=10)
    assert len(items) == 0
    assert len(change_repo.acquire_pending(limit=10)) == 1


def test_tantivy_pending_apply_backpressure_reduces_batch_limit(tmp_path: Path, monkeypatch) -> None:
    """pending 큐 압력이 높으면 적용 배치 상한이 자동 축소되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-backpressure"
    repo_dir.mkdir()
    workspace = WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-backpressure", indexed_at=None, is_active=True)
    change_repo = CandidateIndexChangeRepository(db_path)
    backend = TantivyCandidateBackend(
        config=CandidateSearchConfig(max_file_size_bytes=512 * 1024),
        index_root=tmp_path / "candidate-index-backpressure",
        change_repo=change_repo,
        max_pending_apply_per_search=60,
        min_pending_apply_on_pressure=24,
        max_maintenance_ms_per_search=1000,
    )

    captured_limit = {"value": 0}

    def _high_pressure_count() -> int:
        return 6000

    def _capture_acquire_pending(limit: int):  # type: ignore[no-untyped-def]
        captured_limit["value"] = limit
        return []

    monkeypatch.setattr(change_repo, "count_pending_changes", _high_pressure_count)
    monkeypatch.setattr(change_repo, "acquire_pending", _capture_acquire_pending)

    _ = backend.search(workspaces=[workspace], query="alpha", limit=10)

    assert captured_limit["value"] == 24
