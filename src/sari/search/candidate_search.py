"""후보 파일 검색 레이어를 구현한다."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import tantivy
from tantivy import Document

from sari.core.text_decode import decode_bytes_with_policy
from sari.core.models import CandidateFileDTO, CandidateIndexChangeDTO, SearchErrorDTO, WorkspaceDTO, now_iso8601_utc
from sari.db.repositories.candidate_index_change_repository import CandidateIndexChangeRepository
from sari.search.error_policy import classify_search_error

log = logging.getLogger(__name__)


class CandidateBackendError(Exception):
    """후보 검색 백엔드 실행 오류를 표현한다."""


@dataclass(frozen=True)
class CandidateSearchConfig:
    """후보 검색 동작 설정을 표현한다."""

    max_file_size_bytes: int


@dataclass(frozen=True)
class IndexedFileStateDTO:
    """Tantivy 증분 동기화용 파일 상태를 표현한다."""

    mtime_ns: int
    size_bytes: int
    doc_id: str
    file_hash: str


@dataclass(frozen=True)
class PendingApplyOutcomeDTO:
    """pending 변경 적용 결과를 표현한다."""

    applied_ids: list[int]
    failed_rows: list["PendingApplyFailureDTO"]
    delete_probes: list["DeleteVisibilityProbeDTO"]
    mutated: bool


@dataclass(frozen=True)
class PendingApplyFailureDTO:
    """pending 적용 실패 레코드를 표현한다."""

    change_id: int
    message: str


@dataclass(frozen=True)
class DeleteVisibilityProbeDTO:
    """삭제 반영 후 가시성 검증 대상을 표현한다."""

    change_id: int
    repo_root: str
    relative_path: str


class CandidateBackend(Protocol):
    """후보 검색 백엔드 프로토콜을 정의한다."""

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """후보 파일 목록을 반환한다."""


class ScanCandidateBackend:
    """파일시스템 스캔 기반 후보 검색 백엔드다."""

    def __init__(self, config: CandidateSearchConfig) -> None:
        """설정값을 주입한다."""
        self._config = config

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """워크스페이스에서 질의어를 포함한 파일 후보를 반환한다."""
        normalized_query = query.strip().lower()
        if normalized_query == "":
            return []

        results: list[CandidateFileDTO] = []
        for workspace in workspaces:
            root = Path(workspace.path)
            if not root.exists() or not root.is_dir():
                continue

            for file_path in self._iter_source_files(root):
                if len(results) >= limit:
                    return results
                score, file_hash = self._analyze_file(file_path, normalized_query)
                if score <= 0.0 or file_hash is None:
                    continue
                results.append(
                    CandidateFileDTO(
                        repo_root=str(root),
                        relative_path=str(file_path.relative_to(root).as_posix()),
                        score=score,
                        file_hash=file_hash,
                    )
                )
        return results

    def _iter_source_files(self, root: Path) -> list[Path]:
        """검색 대상 소스 파일 목록을 반환한다."""
        files: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix in {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".kts", ".go", ".rs"}:
                files.append(path)
        return files

    def _analyze_file(self, file_path: Path, normalized_query: str) -> tuple[float, str | None]:
        """파일 텍스트 기반 점수와 콘텐츠 해시를 계산한다."""
        try:
            if file_path.stat().st_size > self._config.max_file_size_bytes:
                return 0.0, None
            raw_bytes = file_path.read_bytes()
            decoded = decode_bytes_with_policy(raw_bytes)
            raw_content = decoded.text
        except OSError:
            return 0.0, None

        lowered = raw_content.lower()
        if normalized_query not in lowered:
            return 0.0, None

        score = float(lowered.count(normalized_query))
        # 원본 바이트 기반 해시를 사용해 인코딩/런타임 차이에 따른 흔들림을 줄인다.
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        return score, content_hash


class TantivyCandidateBackend:
    """Tantivy 인덱스를 이용한 후보 검색 백엔드다."""

    def __init__(
        self,
        config: CandidateSearchConfig,
        index_root: Path,
        change_repo: CandidateIndexChangeRepository | None = None,
        sync_interval_sec: int = 1800,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """인덱스 경로와 설정을 주입한다."""
        self._config = config
        self._change_repo = change_repo
        self._index_root = index_root
        self._index_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index = self._build_index()
        self._writer: _TantivyWriterProtocol | None = None
        self._indexed_roots: set[str] = set()
        self._indexed_files: dict[tuple[str, str], IndexedFileStateDTO] = {}
        self._index_dirty = True
        self._bootstrap_completed = False
        self._last_sync_at = 0.0
        self._sync_interval_sec = max(1, sync_interval_sec)
        self._clock = clock if clock is not None else time.monotonic

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> list[CandidateFileDTO]:
        """Tantivy 인덱스 질의로 후보 파일을 조회한다."""
        normalized_query = query.strip().lower()
        if normalized_query == "":
            return []
        try:
            with self._lock:
                now = self._clock()
                mutated = False
                apply_outcome: PendingApplyOutcomeDTO | None = None
                if self._change_repo is not None:
                    if not self._bootstrap_completed and not self._change_repo.has_pending_changes() and len(self._indexed_files) == 0:
                        # 초기 변경로그가 없는 경우에만 1회 bootstrap 동기화를 허용한다.
                        mutated = self._sync_index(workspaces) or mutated
                    apply_outcome = self._apply_pending_changes(workspaces=workspaces)
                    mutated = apply_outcome.mutated or mutated
                    if (now - self._last_sync_at) >= float(self._sync_interval_sec):
                        mutated = self._reconcile_index_state(workspaces=workspaces) or mutated
                        self._last_sync_at = now
                    self._index_dirty = False
                    self._bootstrap_completed = True
                elif self._index_dirty or (now - self._last_sync_at) >= float(self._sync_interval_sec):
                    mutated = self._sync_index(workspaces) or mutated
                    self._last_sync_at = now
                    self._index_dirty = False

                if mutated:
                    self._get_writer().commit()
                self._index.reload()
                if apply_outcome is not None:
                    apply_outcome = self._merge_delete_visibility_failures(apply_outcome=apply_outcome)
                    self._finalize_pending_apply(apply_outcome=apply_outcome)
                searcher = self._index.searcher()
                parsed = self._parse_query_with_fallback(normalized_query)
                result = searcher.search(parsed, limit)
                items: list[CandidateFileDTO] = []
                for score, address in result.hits:
                    doc = searcher.doc(address)
                    repo_root = _first_value_as_string(doc, "repo_root")
                    relative_path = _first_value_as_string(doc, "relative_path")
                    file_hash = _first_value_as_string(doc, "file_hash")
                    if repo_root is None or relative_path is None or file_hash is None:
                        continue
                    items.append(
                        CandidateFileDTO(
                            repo_root=repo_root,
                            relative_path=relative_path,
                            score=float(score),
                            file_hash=file_hash,
                        )
                    )
                return items
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            raise CandidateBackendError(f"tantivy backend failed: {exc}") from exc

    def mark_dirty(self) -> None:
        """외부 변경 이벤트 수신 시 인덱스 dirty 상태를 표시한다."""
        with self._lock:
            self._index_dirty = True

    def enqueue_upsert_change(self, change: CandidateIndexChangeDTO) -> None:
        """파일 upsert 변경 로그를 큐에 적재한다."""
        if self._change_repo is None:
            self.mark_dirty()
            return
        self._change_repo.enqueue_upsert(change)

    def enqueue_delete_change(self, repo_root: str, relative_path: str, reason: str) -> None:
        """파일 delete 변경 로그를 큐에 적재한다."""
        if self._change_repo is None:
            self.mark_dirty()
            return
        self._change_repo.enqueue_delete(
            repo_root=repo_root,
            relative_path=relative_path,
            event_source=reason,
            recorded_at=now_iso8601_utc(),
        )

    def _parse_query_with_fallback(self, normalized_query: str) -> object:
        """특수문자 입력을 포함해 Tantivy 쿼리를 안전하게 파싱한다."""
        fields = ["content", "relative_path"]
        try:
            return self._index.parse_query(normalized_query, fields)
        except (ValueError, RuntimeError, TypeError):
            escaped_query = _escape_tantivy_query(normalized_query)
            if escaped_query != "":
                try:
                    return self._index.parse_query(escaped_query, fields)
                except (ValueError, RuntimeError, TypeError):
                    log.debug("tantivy escaped query parse 실패(query=%s)", normalized_query)
            tokenized_query = _tokenize_query_for_fallback(normalized_query)
            if tokenized_query == "":
                raise
            return self._index.parse_query(tokenized_query, fields)

    def _build_index(self) -> tantivy.Index:
        """Tantivy 인덱스를 초기화한다."""
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("doc_id", stored=True)
        builder.add_text_field("repo_root", stored=True)
        builder.add_text_field("relative_path", stored=True)
        builder.add_text_field("file_hash", stored=True)
        builder.add_text_field("content", stored=False)
        schema = builder.build()
        if _has_index_metadata(self._index_root):
            return tantivy.Index(schema, path=str(self._index_root))
        return tantivy.Index(schema, path=str(self._index_root))

    def _sync_index(self, workspaces: list[WorkspaceDTO]) -> bool:
        """워크스페이스 변경을 인덱스에 반영한다."""
        active_roots: set[str] = set()
        observed_keys: set[tuple[str, str]] = set()
        mutated = False
        for workspace in workspaces:
            root = Path(workspace.path).resolve()
            if not root.exists() or not root.is_dir():
                continue
            root_text = str(root)
            active_roots.add(root_text)
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if any(part.startswith(".") for part in path.parts):
                    continue
                suffix = path.suffix.lower()
                if suffix not in {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".kts", ".go", ".rs"}:
                    continue
                stat = path.stat()
                if stat.st_size > self._config.max_file_size_bytes:
                    continue
                relative_path = str(path.relative_to(root).as_posix())
                indexed_key = (root_text, relative_path)
                observed_keys.add(indexed_key)
                previous = self._indexed_files.get(indexed_key)
                if previous is not None and previous.mtime_ns == stat.st_mtime_ns and previous.size_bytes == stat.st_size:
                    continue

                raw = path.read_bytes()
                file_hash = hashlib.sha256(raw).hexdigest()
                doc_id = hashlib.sha256(f"{root_text}\0{relative_path}".encode("utf-8")).hexdigest()
                if previous is not None:
                    self._get_writer().delete_documents_by_term("doc_id", previous.doc_id)
                self._get_writer().add_document(
                    Document(
                        doc_id=doc_id,
                        repo_root=root_text,
                        relative_path=relative_path,
                        file_hash=file_hash,
                        content=decode_bytes_with_policy(raw).text.lower(),
                    )
                )
                self._indexed_files[indexed_key] = IndexedFileStateDTO(
                    mtime_ns=stat.st_mtime_ns,
                    size_bytes=stat.st_size,
                    doc_id=doc_id,
                    file_hash=file_hash,
                )
                mutated = True

        removed_keys: list[tuple[str, str]] = []
        for indexed_key, state in self._indexed_files.items():
            repo_root, _ = indexed_key
            if repo_root not in active_roots or indexed_key not in observed_keys:
                self._get_writer().delete_documents_by_term("doc_id", state.doc_id)
                removed_keys.append(indexed_key)
                mutated = True
        for indexed_key in removed_keys:
            self._indexed_files.pop(indexed_key, None)
        self._indexed_roots = active_roots
        return mutated

    def _apply_pending_changes(self, workspaces: list[WorkspaceDTO], batch_limit: int = 1000) -> PendingApplyOutcomeDTO:
        """pending 변경 로그를 Tantivy 인덱스에 증분 반영한다."""
        if self._change_repo is None:
            return PendingApplyOutcomeDTO(applied_ids=[], failed_rows=[], delete_probes=[], mutated=False)
        pending = self._change_repo.acquire_pending(limit=batch_limit)
        if len(pending) == 0:
            return PendingApplyOutcomeDTO(applied_ids=[], failed_rows=[], delete_probes=[], mutated=False)
        workspace_roots = {str(Path(item.path).resolve()): item for item in workspaces}
        applied_ids: list[int] = []
        failed_rows: list[PendingApplyFailureDTO] = []
        delete_probes: list[DeleteVisibilityProbeDTO] = []
        mutated = False
        for change in pending:
            try:
                if change.change_type == "UPSERT":
                    self._apply_upsert_change(workspace_roots=workspace_roots, change=change)
                    mutated = True
                elif change.change_type == "DELETE":
                    self._apply_delete_change(repo_root=change.repo_root, relative_path=change.relative_path)
                    delete_probes.append(
                        DeleteVisibilityProbeDTO(
                            change_id=change.change_id,
                            repo_root=str(Path(change.repo_root).resolve()),
                            relative_path=change.relative_path,
                        )
                    )
                    mutated = True
                else:
                    raise ValueError(f"unsupported change_type: {change.change_type}")
                applied_ids.append(change.change_id)
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                failed_rows.append(PendingApplyFailureDTO(change_id=change.change_id, message=f"candidate apply failed: {exc}"))
        return PendingApplyOutcomeDTO(applied_ids=applied_ids, failed_rows=failed_rows, delete_probes=delete_probes, mutated=mutated)

    def _apply_upsert_change(self, workspace_roots: dict[str, WorkspaceDTO], change) -> None:  # type: ignore[no-untyped-def]
        """upsert 변경 로그를 인덱스 문서로 반영한다."""
        if change.absolute_path is None or change.content_hash is None or change.mtime_ns is None or change.size_bytes is None:
            raise ValueError("upsert payload is incomplete")
        repo_root = str(Path(change.repo_root).resolve())
        if repo_root not in workspace_roots:
            raise ValueError("repo is not active workspace")
        file_path = Path(change.absolute_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            self._apply_delete_change(repo_root=repo_root, relative_path=change.relative_path)
            return
        stat = file_path.stat()
        if stat.st_mtime_ns != change.mtime_ns or stat.st_size != change.size_bytes:
            raise ValueError("mtime/size mismatch")
        raw = file_path.read_bytes()
        computed_hash = hashlib.sha256(raw).hexdigest()
        if computed_hash != change.content_hash:
            raise ValueError("content hash mismatch")

        indexed_key = (repo_root, change.relative_path)
        previous = self._indexed_files.get(indexed_key)
        doc_id = self._build_doc_id(repo_root=repo_root, relative_path=change.relative_path)
        if previous is not None:
            self._get_writer().delete_documents_by_term("doc_id", previous.doc_id)
        self._get_writer().add_document(
            Document(
                doc_id=doc_id,
                repo_root=repo_root,
                relative_path=change.relative_path,
                file_hash=computed_hash,
                content=decode_bytes_with_policy(raw).text.lower(),
            )
        )
        self._indexed_files[indexed_key] = IndexedFileStateDTO(
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
            doc_id=doc_id,
            file_hash=computed_hash,
        )
        self._indexed_roots.add(repo_root)

    def _apply_delete_change(self, repo_root: str, relative_path: str) -> str:
        """delete 변경 로그를 인덱스에서 반영한다."""
        normalized_root = str(Path(repo_root).resolve())
        indexed_key = (normalized_root, relative_path)
        previous = self._indexed_files.pop(indexed_key, None)
        doc_id = previous.doc_id if previous is not None else self._build_doc_id(repo_root=normalized_root, relative_path=relative_path)
        self._get_writer().delete_documents_by_term("doc_id", doc_id)
        return doc_id

    def _reconcile_index_state(self, workspaces: list[WorkspaceDTO]) -> bool:
        """주기적으로 비활성 저장소 문서를 정리한다."""
        active_roots = {str(Path(item.path).resolve()) for item in workspaces if Path(item.path).exists()}
        removed_keys: list[tuple[str, str]] = []
        mutated = False
        for indexed_key, state in self._indexed_files.items():
            repo_root, _ = indexed_key
            if repo_root not in active_roots:
                self._get_writer().delete_documents_by_term("doc_id", state.doc_id)
                removed_keys.append(indexed_key)
                mutated = True
        for indexed_key in removed_keys:
            self._indexed_files.pop(indexed_key, None)
        self._indexed_roots = active_roots
        return mutated

    def _merge_delete_visibility_failures(self, apply_outcome: PendingApplyOutcomeDTO) -> PendingApplyOutcomeDTO:
        """삭제 요청이 reload 이후에도 보이면 실패로 승격한다."""
        if len(apply_outcome.delete_probes) == 0:
            return apply_outcome
        failed_rows = list(apply_outcome.failed_rows)
        failed_ids: set[int] = {row.change_id for row in failed_rows}
        for probe in apply_outcome.delete_probes:
            if probe.change_id in failed_ids:
                continue
            if self._is_repo_path_visible(repo_root=probe.repo_root, relative_path=probe.relative_path):
                failed_rows.append(
                    PendingApplyFailureDTO(
                        change_id=probe.change_id,
                        message=(
                            "candidate delete visibility check failed: "
                            f"repo_root={probe.repo_root}, relative_path={probe.relative_path}"
                        ),
                    )
                )
                failed_ids.add(probe.change_id)
        applied_ids = [change_id for change_id in apply_outcome.applied_ids if change_id not in failed_ids]
        return PendingApplyOutcomeDTO(
            applied_ids=applied_ids,
            failed_rows=failed_rows,
            delete_probes=apply_outcome.delete_probes,
            mutated=apply_outcome.mutated,
        )

    def _is_repo_path_visible(self, repo_root: str, relative_path: str) -> bool:
        """특정 repo/path 문서가 검색 가능한 상태인지 확인한다."""
        try:
            escaped_repo_root = _escape_tantivy_phrase(repo_root)
            escaped_relative_path = _escape_tantivy_phrase(relative_path)
            parsed = self._index.parse_query(
                f'repo_root:"{escaped_repo_root}" AND relative_path:"{escaped_relative_path}"',
                ["repo_root", "relative_path"],
            )
        except ValueError as exc:
            raise ValueError(f"candidate delete visibility query parse failed: {exc}") from exc
        searcher = self._index.searcher()
        result = searcher.search(parsed, 1)
        return len(result.hits) > 0

    def _finalize_pending_apply(self, apply_outcome: PendingApplyOutcomeDTO) -> None:
        """pending 적용 결과를 DB 상태와 오류 정책으로 확정한다."""
        if self._change_repo is None:
            return
        now_iso = now_iso8601_utc()
        for change_id in apply_outcome.applied_ids:
            self._change_repo.mark_applied(change_id=change_id, updated_at=now_iso)
        for failed_row in apply_outcome.failed_rows:
            self._change_repo.mark_failed(
                change_id=failed_row.change_id,
                error_message=failed_row.message,
                updated_at=now_iso,
            )
        if len(apply_outcome.failed_rows) > 0:
            first_message = apply_outcome.failed_rows[0].message
            raise ValueError(first_message)

    def _get_writer(self) -> "_TantivyWriterProtocol":
        """필요 시 Tantivy writer를 지연 생성한다."""
        if self._writer is not None:
            return self._writer
        try:
            self._writer = self._index.writer(100_000_000)
        except ValueError as exc:
            lowered = str(exc).lower()
            if "lockbusy" in lowered or "failed to acquire lockfile" in lowered:
                raise CandidateBackendError(
                    "ERR_TANTIVY_LOCK_BUSY: tantivy writer lock is busy; use daemon proxy mode (sari mcp stdio)"
                ) from exc
            raise
        return self._writer

    @staticmethod
    def _build_doc_id(repo_root: str, relative_path: str) -> str:
        """문서 식별자 해시를 생성한다."""
        return hashlib.sha256(f"{repo_root}\0{relative_path}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateSearchResultDTO:
    """후보 검색 결과와 메타를 함께 표현한다."""

    candidates: list[CandidateFileDTO]
    source: str
    errors: list[SearchErrorDTO]


class CandidateSearchService:
    """파일 후보를 빠르게 수집하는 서비스다."""

    def __init__(
        self,
        backend: CandidateBackend,
        fallback_backend: CandidateBackend | None = None,
    ) -> None:
        """후보 검색 백엔드와 fallback 백엔드를 주입한다."""
        self._backend = backend
        self._fallback_backend = fallback_backend

    @classmethod
    def build_default(
        cls,
        *,
        max_file_size_bytes: int,
        index_root: Path,
        backend_mode: str,
        enable_scan_fallback: bool,
        change_repo: CandidateIndexChangeRepository | None = None,
    ) -> CandidateSearchService:
        """설정 기반 기본 후보 검색 서비스를 생성한다."""
        config = CandidateSearchConfig(max_file_size_bytes=max_file_size_bytes)
        scan_backend = ScanCandidateBackend(config=config)
        if backend_mode == "scan":
            return cls(backend=scan_backend, fallback_backend=None)
        tantivy_backend = TantivyCandidateBackend(config=config, index_root=index_root, change_repo=change_repo)
        return cls(
            backend=tantivy_backend,
            fallback_backend=scan_backend if enable_scan_fallback else None,
        )

    def search(self, workspaces: list[WorkspaceDTO], query: str, limit: int) -> CandidateSearchResultDTO:
        """워크스페이스에서 후보 파일을 조회한다."""
        try:
            candidates = self._backend.search(workspaces=workspaces, query=query, limit=limit)
            source = "tantivy" if isinstance(self._backend, TantivyCandidateBackend) else "scan"
            return CandidateSearchResultDTO(candidates=candidates, source=source, errors=[])
        except CandidateBackendError as primary_exc:
            log.error("후보 검색 주백엔드 실패(query=%s): %s", query, primary_exc)
            code = _resolve_candidate_error_code(primary_exc)
            if self._fallback_backend is None:
                return CandidateSearchResultDTO(
                    candidates=[],
                    source="backend_error",
                    errors=[
                        SearchErrorDTO(
                            code=code,
                            message=f"candidate backend failed: {primary_exc}",
                            severity=classify_search_error(code),
                            origin="candidate",
                        )
                    ],
                )
            try:
                fallback_candidates = self._fallback_backend.search(workspaces=workspaces, query=query, limit=limit)
                log.error("후보 검색 fallback 전환(query=%s): %s", query, primary_exc)
                return CandidateSearchResultDTO(
                    candidates=fallback_candidates,
                    source="scan_fallback",
                    errors=[
                        SearchErrorDTO(
                            code=code,
                            message=f"fallback used: {primary_exc}",
                            severity=classify_search_error(code),
                            origin="candidate",
                        )
                    ],
                )
            except CandidateBackendError as fallback_exc:
                code = _resolve_candidate_error_code(fallback_exc)
                log.error("후보 검색 주/보조 백엔드 모두 실패(query=%s): %s / %s", query, primary_exc, fallback_exc)
                return CandidateSearchResultDTO(
                    candidates=[],
                    source="backend_error",
                    errors=[
                        SearchErrorDTO(
                            code=code,
                            message=f"candidate backend failed: {primary_exc}; fallback failed: {fallback_exc}",
                            severity=classify_search_error(code),
                            origin="candidate",
                        )
                    ],
                )

    def filter_workspaces_by_repo(self, workspaces: list[WorkspaceDTO], repo_root: str) -> list[WorkspaceDTO]:
        """repo 필터 기준으로 후보 검색 대상을 단일 저장소로 축소한다."""
        normalized_repo = str(Path(repo_root).resolve())
        return [workspace for workspace in workspaces if str(Path(workspace.path).resolve()) == normalized_repo]

    def mark_repo_dirty(self, repo_root: str) -> None:
        """저장소 단위 변경 신호를 후보 인덱스 백엔드에 전달한다."""
        _ = repo_root
        if isinstance(self._backend, TantivyCandidateBackend):
            self._backend.mark_dirty()

    def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
        """파일 단위 변경 신호를 후보 인덱스 백엔드에 전달한다."""
        _ = (repo_root, relative_path)
        if isinstance(self._backend, TantivyCandidateBackend):
            self._backend.mark_dirty()

    def record_upsert(self, change: CandidateIndexChangeDTO) -> None:
        """파일 upsert 변경 신호를 후보 인덱스 백엔드에 전달한다."""
        if isinstance(self._backend, TantivyCandidateBackend):
            self._backend.enqueue_upsert_change(change)
            return
        self.mark_file_dirty(repo_root=change.repo_root, relative_path=change.relative_path)

    def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
        """파일 delete 변경 신호를 후보 인덱스 백엔드에 전달한다."""
        if isinstance(self._backend, TantivyCandidateBackend):
            self._backend.enqueue_delete_change(repo_root=repo_root, relative_path=relative_path, reason=reason)
            return
        self.mark_file_dirty(repo_root=repo_root, relative_path=relative_path)


def _has_index_metadata(index_root: Path) -> bool:
    """인덱스 메타 파일 존재 여부를 확인한다."""
    return (index_root / "meta.json").exists()


class _TantivyWriterProtocol(Protocol):
    """Tantivy writer가 제공해야 하는 최소 메서드 집합."""

    def commit(self) -> object:
        """변경 사항을 커밋한다."""

    def delete_documents_by_term(self, field_name: str, value: str) -> object:
        """term 기반 문서 삭제를 수행한다."""

    def add_document(self, document: Document) -> object:
        """문서를 인덱스에 추가한다."""


def _first_value_as_string(doc: Document, field_name: str) -> str | None:
    """Document 필드의 첫 번째 값을 문자열로 반환한다."""
    values = doc.get_all(field_name)
    if len(values) == 0:
        return None
    raw = values[0]
    if isinstance(raw, str):
        return raw
    return str(raw)


def _escape_tantivy_query(raw_query: str) -> str:
    """Tantivy query parser 특수문자를 이스케이프한다."""
    escaped_parts: list[str] = []
    for character in raw_query:
        if character in {"+", "-", "&", "|", "!", "(", ")", "{", "}", "[", "]", "^", "\"", "~", "*", "?", ":", "\\"}:
            escaped_parts.append("\\")
        escaped_parts.append(character)
    return "".join(escaped_parts).strip()


def _escape_tantivy_phrase(raw_value: str) -> str:
    """Tantivy phrase 문자열을 안전하게 이스케이프한다."""
    return raw_value.replace("\\", "\\\\").replace("\"", "\\\"")


def _tokenize_query_for_fallback(raw_query: str) -> str:
    """특수문자 질의를 단순 토큰 질의로 변환한다."""
    tokens = re.findall(r"[0-9a-zA-Z_]+", raw_query)
    return " ".join(tokens).strip()


def _resolve_candidate_error_code(exc: CandidateBackendError) -> str:
    """후보 검색 예외에서 오류 코드를 도출한다."""
    message = str(exc)
    if message.startswith("ERR_TANTIVY_LOCK_BUSY:"):
        return "ERR_TANTIVY_LOCK_BUSY"
    return "ERR_CANDIDATE_BACKEND"
