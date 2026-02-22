from __future__ import annotations
import hashlib
import logging
import queue
import sqlite3
import concurrent.futures
import threading
import time
import traceback
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Protocol
from solidlsp.ls_config import Language
from sari.core.exceptions import CollectionError, DaemonError, ErrorContext, ValidationError
from sari.core.config import DEFAULT_COLLECTION_EXCLUDE_GLOBS
from sari.core.language_registry import get_default_collection_extensions, get_enabled_language_names, resolve_language_from_path
from sari.core.text_decode import decode_bytes_with_policy
from sari.core.models import CandidateIndexChangeDTO, CollectionPolicyDTO, CollectionScanRepoResultDTO, CollectionScanResultDTO, CollectedFileL1DTO, FileReadResultDTO, PipelineMetricsDTO, now_iso8601_utc
from sari.db.repositories.file_body_repository import FileBodyDecodeError, FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_location_to_repo_relative, normalize_repo_relative_path
from sari.services.collection import CollectionErrorPolicy, CollectionRuntimePort, EnrichEngine, EventWatcher, FileScanner, PipelineMetricsService, PipelineWorker, RuntimeManager
from sari.services.collection.perf_trace import PerfTracer
from sari.services.collection.repo_support import CollectionRepoSupport, WorkspaceFanoutResolver
from solidlsp.ls_exceptions import SolidLSPException
log = logging.getLogger(__name__)

@dataclass(frozen=True)
class LspExtractionResultDTO:
    symbols: list[dict[str, object]]
    relations: list[dict[str, object]]
    error_message: str | None


@dataclass
class _InflightLspExtractState:
    """동일 LSP 추출 요청의 in-flight 상태를 공유한다."""

    event: threading.Event
    result: LspExtractionResultDTO | None


@dataclass
class _ProbeStateRecord:
    """LSP probe 상태를 key 단위로 관리한다."""

    status: str = "IDLE"
    fail_count: int = 0
    warming_count: int = 0
    next_retry_monotonic: float = 0.0
    last_error_code: str | None = None
    last_error_time_monotonic: float | None = None
    last_seen_monotonic: float = 0.0
    last_trigger: str | None = None
    last_error_message: str | None = None


class LspExtractionBackend(Protocol):

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        ...

class CandidateIndexSink(Protocol):

    def mark_repo_dirty(self, repo_root: str) -> None:
        ...

    def mark_file_dirty(self, repo_root: str, relative_path: str) -> None:
        ...

    def record_upsert(self, change: CandidateIndexChangeDTO) -> None:
        ...

    def record_delete(self, repo_root: str, relative_path: str, reason: str) -> None:
        ...

class VectorIndexSink(Protocol):

    def upsert_file_embedding(self, repo_root: str, relative_path: str, content_hash: str, content_text: str) -> None:
        ...

class SolidLspExtractionBackend:
    """인덱싱 전용 LSP 추출 백엔드.

    LspHub get_or_start/acquire_pool 호출 시 request_kind 인자를 전달하는 계약을 사용한다.
    """

    def __init__(
        self,
        hub: LspHub,
        *,
        probe_workers: int = 4,
        l1_workers: int = 2,
        force_join_ms: int = 300,
        warming_retry_sec: int = 5,
        warming_threshold: int = 6,
        permanent_backoff_sec: int = 1800,
    ) -> None:
        self._hub = hub
        self._perf_tracer = PerfTracer(component="solid_lsp_backend")
        self._prewarmed_keys: set[tuple[Language, str]] = set()
        self._hot_languages_by_repo: dict[str, set[Language]] = {}
        self._prewarm_lock = threading.Lock()
        self._prewarm_key_locks: dict[tuple[Language, str], threading.Lock] = {}
        self._prewarm_key_locks_guard = threading.Lock()
        self._inflight_lock = threading.Lock()
        self._inflight_extracts: dict[tuple[str, str, str], _InflightLspExtractState] = {}
        self._inflight_wait_timeout_sec = 30.0
        self._probe_lock = threading.Lock()
        self._probe_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(probe_workers)),
            thread_name_prefix="lsp-probe",
        )
        self._l1_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(l1_workers)),
            thread_name_prefix="lsp-probe-l1",
        )
        self._probe_stopping = False
        self._probe_inflight: dict[tuple[str, Language], concurrent.futures.Future[None]] = {}
        self._probe_inflight_phase: dict[tuple[str, Language], str] = {}
        self._probe_state: dict[tuple[str, Language], _ProbeStateRecord] = {}
        self._probe_force_join_sec = max(0.0, float(max(0, int(force_join_ms))) / 1000.0)
        self._probe_warming_retry_sec = max(1.0, float(max(1, int(warming_retry_sec))))
        self._probe_warming_threshold = max(1, int(warming_threshold))
        self._probe_permanent_backoff_sec = max(60.0, float(max(60, int(permanent_backoff_sec))))
        self._probe_unavailable_backoff_initial_sec = 180.0
        self._probe_unavailable_backoff_mid_sec = 600.0
        self._probe_unavailable_backoff_cap_sec = max(self._probe_permanent_backoff_sec, 1800.0)
        self._probe_timeout_backoff_initial_sec = 30.0
        self._probe_timeout_backoff_mid_sec = 60.0
        self._probe_timeout_backoff_cap_sec = 120.0
        self._probe_timeout_window_sec = 30.0
        self._probe_trigger_counts: dict[str, int] = {}

    def extract(self, repo_root: str, relative_path: str, content_hash: str) -> LspExtractionResultDTO:
        normalized_relative_path = normalize_repo_relative_path(relative_path)
        normalized_repo_root = str(Path(repo_root).resolve())
        dedupe_key = (normalized_repo_root, normalized_relative_path, content_hash)
        with self._inflight_lock:
            inflight_state = self._inflight_extracts.get(dedupe_key)
            if inflight_state is None:
                inflight_state = _InflightLspExtractState(event=threading.Event(), result=None)
                self._inflight_extracts[dedupe_key] = inflight_state
                leader = True
            else:
                leader = False
        if not leader:
            inflight_state.event.wait(self._inflight_wait_timeout_sec)
            with self._inflight_lock:
                finished = self._inflight_extracts.get(dedupe_key)
                if finished is None:
                    result = inflight_state.result
                else:
                    result = finished.result
            if result is not None:
                return result
            return LspExtractionResultDTO(
                symbols=[],
                relations=[],
                error_message=(
                    "ERR_LSP_INFLIGHT_WAIT_TIMEOUT: "
                    f"repo={normalized_repo_root}, path={normalized_relative_path}"
                ),
            )
        result: LspExtractionResultDTO | None = None
        try:
            with self._perf_tracer.span("extract._extract_once", phase="l3_extract", repo_root=normalized_repo_root):
                result = self._extract_once(repo_root=repo_root, normalized_relative_path=normalized_relative_path)
            if result.error_message is not None:
                error_code = _extract_error_code_from_message(result.error_message)
                self._record_probe_state_from_extract_error(
                    repo_root=normalized_repo_root,
                    relative_path=normalized_relative_path,
                    error_code=error_code,
                    error_message=result.error_message,
                )
                if self._should_force_recover_from_extract_error(
                    repo_root=normalized_repo_root,
                    relative_path=normalized_relative_path,
                    error_code=error_code,
                ):
                    self.invalidate_probe_ready_for_file(repo_root=normalized_repo_root, relative_path=normalized_relative_path)
                    self.schedule_probe_for_file(
                        repo_root=normalized_repo_root,
                        relative_path=normalized_relative_path,
                        force=True,
                        trigger="force",
                    )
                    with self._perf_tracer.span("extract._extract_once_retry", phase="l3_extract", repo_root=normalized_repo_root):
                        result = self._extract_once(repo_root=repo_root, normalized_relative_path=normalized_relative_path)
            return result
        except (DaemonError, ValidationError, RuntimeError, OSError, ValueError, TypeError, concurrent.futures.TimeoutError) as exc:
            return LspExtractionResultDTO(
                symbols=[],
                relations=[],
                error_message=f"LSP 추출 실패: {exc}",
            )
        finally:
            with self._inflight_lock:
                state = self._inflight_extracts.get(dedupe_key)
                if state is not None:
                    state.result = result
                    state.event.set()
                    del self._inflight_extracts[dedupe_key]

    def _extract_once(self, repo_root: str, normalized_relative_path: str) -> LspExtractionResultDTO:
        try:
            language = self._hub.resolve_language(normalized_relative_path)
            with self._perf_tracer.span("extract_once.ensure_prewarm", phase="l3_extract", repo_root=repo_root, language=language.value):
                self._ensure_prewarm(language=language, repo_root=repo_root)
            with self._perf_tracer.span("extract_once.get_or_start", phase="l3_extract", repo_root=repo_root, language=language.value, request_kind="indexing"):
                lsp = self._hub.get_or_start(language=language, repo_root=repo_root, request_kind="indexing")
            with self._acquire_l1_probe_slot():
                with self._perf_tracer.span("extract_once.document_symbol_request", phase="l3_extract", repo_root=repo_root, language=language.value):
                    document_symbols = lsp.request_document_symbols(normalized_relative_path).iter_symbols()
                    raw_symbols = list(document_symbols)
        except SolidLSPException as exc:
            message = str(exc)
            if _is_workspace_mismatch_error(message):
                return LspExtractionResultDTO(
                    symbols=[],
                    relations=[],
                    error_message=f'ERR_LSP_WORKSPACE_MISMATCH: repo={repo_root}, path={normalized_relative_path}, reason={message}',
                )
            if 'ERR_LSP_SYNC_OPEN_FAILED' in message:
                return LspExtractionResultDTO(symbols=[], relations=[], error_message=f'ERR_LSP_SYNC_OPEN_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}')
            if 'ERR_LSP_SYNC_CHANGE_FAILED' in message:
                return LspExtractionResultDTO(symbols=[], relations=[], error_message=f'ERR_LSP_SYNC_CHANGE_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}')
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=f'ERR_LSP_DOCUMENT_SYMBOL_FAILED: repo={repo_root}, path={normalized_relative_path}, reason={message}')
        except (DaemonError, RuntimeError, OSError, ValueError, TypeError, concurrent.futures.TimeoutError) as exc:
            return LspExtractionResultDTO(symbols=[], relations=[], error_message=f'LSP 추출 실패: {exc}')
        with self._perf_tracer.span(
            "extract_once.normalize_symbols",
            phase="l3_extract",
            repo_root=repo_root,
            language=(language.value if "language" in locals() else "unknown"),
        ):
            symbols: list[dict[str, object]] = []
            for raw in raw_symbols:
                if not isinstance(raw, dict):
                    continue
                location = raw.get('location')
                resolved_relative_path = normalized_relative_path
                if isinstance(location, dict):
                    resolved_relative_path = normalize_location_to_repo_relative(
                        location=location,
                        fallback_relative_path=normalized_relative_path,
                        repo_root=repo_root,
                    )
                location = raw.get('location')
                if not isinstance(location, dict):
                    location = {}
                range_data = location.get('range')
                line = 0
                end_line = 0
                if isinstance(range_data, dict):
                    start_data = range_data.get('start')
                    end_data = range_data.get('end')
                    if isinstance(start_data, dict):
                        line = int(start_data.get('line', 0))
                    if isinstance(end_data, dict):
                        end_line = int(end_data.get('line', line))
                symbol_name = str(raw.get('name', ''))
                symbol_kind = str(raw.get('kind', ''))
                parent_symbol = raw.get('parent')
                parent_symbol_key = self._build_symbol_key(
                    repo_root=repo_root,
                    relative_path=resolved_relative_path,
                    symbol=parent_symbol,
                    fallback_parent_key=None,
                )
                symbol_key = self._build_symbol_key(
                    repo_root=repo_root,
                    relative_path=resolved_relative_path,
                    symbol=raw,
                    fallback_parent_key=parent_symbol_key,
                )
                symbols.append(
                    {
                        'name': symbol_name,
                        'kind': symbol_kind,
                        'line': line,
                        'end_line': end_line,
                        'symbol_key': symbol_key,
                        'parent_symbol_key': parent_symbol_key,
                        'depth': self._resolve_symbol_depth(raw),
                        'container_name': self._resolve_container_name(raw),
                    }
                )
        return LspExtractionResultDTO(symbols=symbols, relations=[], error_message=None)

    def get_parallelism(self, repo_root: str, language: Language) -> int:
        """현재 언어/레포 풀의 병렬 처리 가능 슬롯 수를 반환한다."""
        running = self._hub.get_running_instance_count(language=language, repo_root=repo_root)
        if running > 0:
            return running
        self._ensure_prewarm(language=language, repo_root=repo_root)
        return max(1, self._hub.get_running_instance_count(language=language, repo_root=repo_root))

    def get_parallelism_for_batch(self, repo_root: str, language: Language, batch_size: int) -> int:
        """배치 크기를 반영해 풀 인스턴스를 확보하고 병렬도를 반환한다."""
        desired = max(1, int(batch_size))
        servers = self._hub.acquire_pool(language=language, repo_root=repo_root, desired=desired, request_kind="indexing")
        return max(1, len(servers))

    def set_bulk_mode(self, repo_root: str, language: Language, enabled: bool) -> None:
        """bulk 인덱싱 모드를 LSP 허브에 전달한다."""
        self._hub.set_bulk_mode(language=language, repo_root=repo_root, enabled=enabled)

    def _resolve_symbol_depth(self, symbol: dict[str, object]) -> int:
        """심볼 parent 체인을 따라 depth를 계산한다."""
        depth = 0
        current = symbol.get('parent')
        while isinstance(current, dict):
            depth += 1
            current = current.get('parent')
        return depth

    def _resolve_container_name(self, symbol: dict[str, object]) -> str | None:
        """부모 심볼 이름을 container_name으로 반환한다."""
        parent = symbol.get('parent')
        if not isinstance(parent, dict):
            return None
        parent_name = parent.get('name')
        if isinstance(parent_name, str) and parent_name.strip() != '':
            return parent_name
        return None

    def _build_symbol_key(
        self,
        repo_root: str,
        relative_path: str,
        symbol: object,
        fallback_parent_key: str | None,
    ) -> str | None:
        """결정적 심볼 키를 생성한다."""
        if not isinstance(symbol, dict):
            return None
        name = symbol.get('name')
        kind = symbol.get('kind')
        if not isinstance(name, str) or not isinstance(kind, str):
            return None
        line = 0
        end_line = 0
        location = symbol.get('location')
        if isinstance(location, dict):
            range_data = location.get('range')
            if isinstance(range_data, dict):
                start_data = range_data.get('start')
                end_data = range_data.get('end')
                if isinstance(start_data, dict):
                    line = int(start_data.get('line', 0))
                if isinstance(end_data, dict):
                    end_line = int(end_data.get('line', line))
        parent_key = fallback_parent_key or 'root'
        key_text = f'{repo_root}:{relative_path}:{name}:{kind}:{line}:{end_line}:{parent_key}'
        return hashlib.sha1(key_text.encode('utf-8')).hexdigest()

    def _ensure_prewarm(self, language: Language, repo_root: str) -> None:
        key = (language, str(Path(repo_root).resolve()))
        with self._prewarm_lock:
            if key in self._prewarmed_keys:
                return
            allowed_languages = self._hot_languages_by_repo.get(key[1])
            if allowed_languages is not None and language not in allowed_languages:
                self._prewarmed_keys.add(key)
                return
        key_lock = self._get_or_create_prewarm_key_lock(key)
        with key_lock:
            with self._prewarm_lock:
                if key in self._prewarmed_keys:
                    return
                allowed_languages = self._hot_languages_by_repo.get(key[1])
                if allowed_languages is not None and language not in allowed_languages:
                    self._prewarmed_keys.add(key)
                    return
            self._hub.prewarm_language_pool(language=language, repo_root=repo_root)
            with self._prewarm_lock:
                self._prewarmed_keys.add(key)

    def _get_or_create_prewarm_key_lock(self, key: tuple[Language, str]) -> threading.Lock:
        with self._prewarm_key_locks_guard:
            existing = self._prewarm_key_locks.get(key)
            if existing is not None:
                return existing
            created = threading.Lock()
            self._prewarm_key_locks[key] = created
            return created

    def configure_hot_languages(self, repo_root: str, languages: set[Language]) -> None:
        normalized = str(Path(repo_root).resolve())
        with self._prewarm_lock:
            self._hot_languages_by_repo[normalized] = set(languages)

    def schedule_probe_for_file(self, repo_root: str, relative_path: str, force: bool = False, trigger: str = "background") -> str:
        """파일 기준 LSP probe를 비동기 스케줄한다."""
        normalized_trigger = trigger.strip().lower() if isinstance(trigger, str) else ""
        if normalized_trigger == "":
            normalized_trigger = "unknown"
        normalized_root = str(Path(repo_root).resolve())
        normalized_relative_path = normalize_repo_relative_path(relative_path)
        language = resolve_language_from_path(file_path=normalized_relative_path)
        if language is None:
            return "unknown_language"
        key = (normalized_root, language)
        now = time.monotonic()
        with self._probe_lock:
            if self._probe_stopping:
                return "stopping"
            inflight = self._probe_inflight.get(key)
            if inflight is not None:
                if force and self._probe_force_join_sec > 0.0:
                    try:
                        inflight.result(timeout=self._probe_force_join_sec)
                    except (concurrent.futures.TimeoutError, RuntimeError, ValueError):
                        return "starting"
                return "inflight"
            state = self._probe_state.get(key)
            if state is None:
                state = _ProbeStateRecord(status="IDLE", last_seen_monotonic=now)
                self._probe_state[key] = state
            state.last_seen_monotonic = now
            state.last_trigger = normalized_trigger
            if state.status == "READY_L0":
                return "ready"
            if state.status == "WORKSPACE_MISMATCH":
                if force:
                    state.status = "IDLE"
                    state.next_retry_monotonic = 0.0
                else:
                    return "workspace_mismatch"
            if state.status == "WARMING":
                if (not force) and now < state.next_retry_monotonic:
                    return "warming"
            if (not force) and now < state.next_retry_monotonic:
                return "cooldown"
            future = self._probe_executor.submit(self._probe_worker, key, normalized_relative_path)
            self._probe_inflight[key] = future
            self._probe_inflight_phase[key] = "probe"
            self._probe_trigger_counts[normalized_trigger] = int(self._probe_trigger_counts.get(normalized_trigger, 0)) + 1
            return "scheduled"

    def invalidate_probe_ready_for_file(self, repo_root: str, relative_path: str) -> None:
        """READY/WARMING 상태를 제거한다."""
        normalized_root = str(Path(repo_root).resolve())
        language = resolve_language_from_path(file_path=normalize_repo_relative_path(relative_path))
        if language is None:
            return
        key = (normalized_root, language)
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                return
            if state.status in {"READY_L0", "WARMING"}:
                state.status = "IDLE"
            state.fail_count = 0
            state.warming_count = 0
            state.next_retry_monotonic = 0.0
            state.last_error_code = None
            state.last_error_message = None

    def shutdown_probe_executor(self) -> None:
        """probe executor를 종료한다."""
        with self._probe_lock:
            self._probe_stopping = True
        self._probe_executor.shutdown(wait=True)
        self._l1_executor.shutdown(wait=True)
        with self._probe_lock:
            self._probe_inflight.clear()
            self._probe_inflight_phase.clear()

    def reset_probe_state(self) -> None:
        """probe 상태를 초기화한다."""
        with self._prewarm_lock:
            self._prewarmed_keys.clear()
        with self._prewarm_key_locks_guard:
            self._prewarm_key_locks.clear()
        with self._probe_lock:
            self._probe_inflight.clear()
            self._probe_inflight_phase.clear()
            self._probe_state.clear()
            self._probe_trigger_counts.clear()

    def reset_lsp_runtime(self) -> None:
        """LSP 런타임을 정리한다."""
        self._hub.stop_all()

    def is_probe_inflight_for_file(self, repo_root: str, relative_path: str) -> bool:
        """(repo, language) probe inflight 여부를 반환한다."""
        normalized_root = str(Path(repo_root).resolve())
        language = resolve_language_from_path(file_path=normalize_repo_relative_path(relative_path))
        if language is None:
            return False
        key = (normalized_root, language)
        with self._probe_lock:
            return key in self._probe_inflight

    def is_l3_permanently_unavailable_for_file(self, repo_root: str, relative_path: str) -> bool:
        """probe 상태 기준으로 현재 시점 L3 시도 불가(TTL active) 여부를 반환한다."""
        normalized_root = str(Path(repo_root).resolve())
        language = resolve_language_from_path(file_path=normalize_repo_relative_path(relative_path))
        if language is None:
            return False
        key = (normalized_root, language)
        now = time.monotonic()
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                return False
            if state.status == "WORKSPACE_MISMATCH":
                return True
            if state.status not in {"COOLDOWN", "UNAVAILABLE_COOLDOWN"}:
                return False
            return now < state.next_retry_monotonic

    def clear_unavailable_state(self, repo_root: str | None = None, language: str | Language | None = None) -> int:
        """LSP unavailable/probe cooldown 상태 캐시를 수동으로 초기화한다."""
        normalized_root = str(Path(repo_root).resolve()) if isinstance(repo_root, str) and repo_root.strip() != "" else None
        target_language: Language | None = None
        if isinstance(language, Language):
            target_language = language
        elif isinstance(language, str) and language.strip() != "":
            raw = language.strip().lower()
            try:
                target_language = Language(raw)
            except ValueError:
                target_language = resolve_language_from_path(file_path=f"file.{raw}")
        cleared = 0
        with self._probe_lock:
            for key, state in list(self._probe_state.items()):
                key_root, key_lang = key
                if normalized_root is not None and key_root != normalized_root:
                    continue
                if target_language is not None and key_lang != target_language:
                    continue
                if state.status not in {"COOLDOWN", "UNAVAILABLE_COOLDOWN", "WORKSPACE_MISMATCH"}:
                    continue
                state.status = "IDLE"
                state.fail_count = 0
                state.warming_count = 0
                state.next_retry_monotonic = 0.0
                state.last_error_code = None
                state.last_error_message = None
                state.last_error_time_monotonic = None
                cleared += 1
        return cleared

    def _probe_worker(self, key: tuple[str, Language], sample_relative_path: str) -> None:
        """단일 key probe worker."""
        now = time.monotonic()
        status = "failure"
        handed_off_to_l1 = False
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                state = _ProbeStateRecord(status="IDLE", last_seen_monotonic=now)
                self._probe_state[key] = state
            state.status = "IDLE"
        try:
            repo_root, language = key
            self._ensure_prewarm(language=language, repo_root=repo_root)
            lsp = self._hub.get_or_start(language=language, repo_root=repo_root, request_kind="indexing")
            with self._probe_lock:
                state = self._probe_state[key]
                state.status = "READY_L0"
                state.fail_count = 0
                state.last_error_code = None
            status = "success"
            with self._probe_lock:
                state = self._probe_state[key]
                state.status = "READY_L0"
                state.warming_count = 0
                state.next_retry_monotonic = 0.0
            if language in {Language.GO, Language.JAVA, Language.KOTLIN}:
                l1_future = self._l1_executor.submit(self._run_l1_probe_tracked, key, sample_relative_path)
                with self._probe_lock:
                    self._probe_inflight[key] = l1_future
                    self._probe_inflight_phase[key] = "l1"
                handed_off_to_l1 = True
        except (SolidLSPException, DaemonError, RuntimeError, OSError, ValueError, TypeError) as exc:
            error_message = str(exc)
            error_code = _extract_error_code_from_message(error_message)
            with self._probe_lock:
                state = self._probe_state[key]
                state.status = "UNAVAILABLE_COOLDOWN" if _is_unavailable_probe_error(error_code) else "COOLDOWN"
                state.fail_count += 1
                state.last_error_code = error_code
                state.last_error_message = error_message
                state.last_error_time_monotonic = now
                state.next_retry_monotonic = now + self._next_probe_retry_backoff_sec(
                    error_code=error_code,
                    fail_count=state.fail_count,
                )
            status = "failure"
        finally:
            with self._probe_lock:
                state = self._probe_state.get(key)
                if state is not None:
                    state.last_seen_monotonic = time.monotonic()
                if not handed_off_to_l1:
                    self._probe_inflight.pop(key, None)
                    self._probe_inflight_phase.pop(key, None)
            _ = status

    def _run_l1_probe_tracked(self, key: tuple[str, Language], sample_relative_path: str) -> None:
        try:
            self._run_l1_probe(key, sample_relative_path)
        finally:
            with self._probe_lock:
                self._probe_inflight.pop(key, None)
                self._probe_inflight_phase.pop(key, None)
                state = self._probe_state.get(key)
                if state is not None:
                    state.last_seen_monotonic = time.monotonic()

    def _run_l1_probe(self, key: tuple[str, Language], sample_relative_path: str) -> None:
        """READY_L0 이후 L1(documentSymbol) probe를 지연 실행한다."""
        now = time.monotonic()
        try:
            repo_root, language = key
            lsp = self._hub.get_or_start(language=language, repo_root=repo_root, request_kind="indexing")
            with self._acquire_l1_probe_slot():
                _ = list(lsp.request_document_symbols(sample_relative_path).iter_symbols())
            with self._probe_lock:
                state = self._probe_state.get(key)
                if state is None:
                    return
                state.status = "READY_L0"
                state.warming_count = 0
                state.next_retry_monotonic = 0.0
                state.last_seen_monotonic = now
        except (SolidLSPException, DaemonError, RuntimeError, OSError, ValueError, TypeError) as exc:
            error_message = str(exc)
            error_code = _extract_error_code_from_message(error_message)
            with self._probe_lock:
                state = self._probe_state.get(key)
                if state is None:
                    return
                if _is_warming_probe_error(code=error_code, message=error_message):
                    state.status = "WARMING"
                    state.warming_count += 1
                    if state.warming_count > self._probe_warming_threshold:
                        state.status = "COOLDOWN"
                        state.fail_count += 1
                        state.next_retry_monotonic = now + _next_transient_backoff_sec(state.fail_count)
                    else:
                        state.next_retry_monotonic = now + self._probe_warming_retry_sec
                else:
                    if error_code == "ERR_LSP_WORKSPACE_MISMATCH":
                        state.status = "WORKSPACE_MISMATCH"
                    else:
                        state.status = "UNAVAILABLE_COOLDOWN" if _is_unavailable_probe_error(error_code) else "COOLDOWN"
                    state.fail_count += 1
                    state.last_error_code = error_code
                    state.last_error_message = error_message
                    state.last_error_time_monotonic = now
                    if state.status == "WORKSPACE_MISMATCH":
                        state.next_retry_monotonic = float("inf")
                    else:
                        state.next_retry_monotonic = now + self._next_probe_retry_backoff_sec(
                            error_code=error_code,
                            fail_count=state.fail_count,
                        )
                state.last_seen_monotonic = now

    def get_runtime_metrics(self) -> dict[str, int]:
        """LSP 허브 런타임 메트릭을 반환한다."""
        metrics = dict(self._hub.get_metrics())
        with self._probe_lock:
            for trigger, count in self._probe_trigger_counts.items():
                metrics[f"probe_trigger_{trigger}_count"] = int(count)
        return metrics

    def get_interactive_pressure(self) -> dict[str, int]:
        """인터랙티브 요청 압력 지표를 반환한다."""
        getter = getattr(self._hub, "get_interactive_pressure", None)
        if callable(getter):
            return getter()
        return {"pending_interactive": 0, "interactive_timeout_count": 0, "interactive_rejected_count": 0}

    @contextmanager
    def _acquire_l1_probe_slot(self):
        """Hub가 세마포어 API를 제공하지 않아도 안전하게 동작한다."""
        acquire = getattr(self._hub, "acquire_l1_probe_slot", None)
        if callable(acquire):
            with acquire():
                yield
            return
        yield

    def _should_force_recover_from_extract_error(self, repo_root: str, relative_path: str, error_code: str) -> bool:
        """실사용 오류 코드에 따라 READY/WARMING 무효화 여부를 판단한다."""
        language = resolve_language_from_path(file_path=relative_path)
        if language is None:
            return False
        key = (repo_root, language)
        now = time.monotonic()
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                return False
            if error_code in {"ERR_BROKEN_PIPE", "ERR_SERVER_EXITED", "ERR_INIT_FAILED"}:
                return state.status in {"READY_L0", "WARMING"}
            if error_code != "ERR_RPC_TIMEOUT":
                return False
            if state.last_error_code == "ERR_RPC_TIMEOUT" and state.last_error_time_monotonic is not None:
                if (now - state.last_error_time_monotonic) <= self._probe_timeout_window_sec:
                    return state.status in {"READY_L0", "WARMING"}
            state.last_error_code = "ERR_RPC_TIMEOUT"
            state.last_error_time_monotonic = now
            return False

    def _record_probe_state_from_extract_error(self, *, repo_root: str, relative_path: str, error_code: str, error_message: str) -> None:
        """L3 extract 실패를 probe 상태에 반영해 반복 startup/요청 폭주를 완화한다."""
        language = resolve_language_from_path(file_path=relative_path)
        if language is None:
            return
        key = (repo_root, language)
        now = time.monotonic()
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                state = _ProbeStateRecord(status="IDLE", last_seen_monotonic=now)
                self._probe_state[key] = state
            state.last_seen_monotonic = now
            state.last_error_code = error_code
            state.last_error_message = error_message
            state.last_error_time_monotonic = now
            if error_code == "ERR_LSP_WORKSPACE_MISMATCH":
                state.status = "WORKSPACE_MISMATCH"
                state.next_retry_monotonic = float("inf")
                return
            if not _is_unavailable_probe_error(error_code):
                return
            state.status = "UNAVAILABLE_COOLDOWN"
            state.fail_count += 1
            state.next_retry_monotonic = now + self._next_probe_retry_backoff_sec(error_code=error_code, fail_count=state.fail_count)

    def _next_probe_retry_backoff_sec(self, *, error_code: str, fail_count: int) -> float:
        """오류 코드/누적 실패 횟수에 따라 probe 재시도 백오프를 계산한다."""
        if error_code in {"ERR_LSP_SERVER_MISSING", "ERR_LSP_SERVER_SPAWN_FAILED", "ERR_RUNTIME_MISMATCH", "ERR_CONFIG_INVALID"}:
            if fail_count <= 2:
                return self._probe_unavailable_backoff_initial_sec
            if fail_count <= 4:
                return self._probe_unavailable_backoff_mid_sec
            return self._probe_unavailable_backoff_cap_sec
        if error_code in {"ERR_LSP_START_TIMEOUT", "ERR_RPC_TIMEOUT", "ERR_LSP_INTERACTIVE_TIMEOUT"}:
            if fail_count <= 1:
                return self._probe_timeout_backoff_initial_sec
            if fail_count == 2:
                return self._probe_timeout_backoff_mid_sec
            return self._probe_timeout_backoff_cap_sec
        return _next_transient_backoff_sec(fail_count)

class FileCollectionService:
    PRIORITY_HIGH = 90
    PRIORITY_MEDIUM = 60
    PRIORITY_LOW = 30
    ENRICH_FLUSH_BATCH_SIZE = 128
    ENRICH_FLUSH_INTERVAL_SEC = 0.5
    ENRICH_FLUSH_MAX_BODY_BYTES = 16 * 1024 * 1024
    SCAN_FLUSH_BATCH_SIZE = 500
    SCAN_FLUSH_INTERVAL_SEC = 0.5
    SCAN_HASH_MAX_WORKERS = 8
    LSP_PREWARM_TOP_LANGUAGE_COUNT = 2
    LSP_PREWARM_MIN_LANGUAGE_FILES = 32
    WATCHER_QUEUE_MAX = 10_000
    WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC = 30
    WORKSPACE_SCAN_BUILD_MARKERS: tuple[str, ...] = (
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "WORKSPACE",
        "MODULE.bazel",
        "nx.json",
        "pnpm-workspace.yaml",
        "turbo.json",
        "composer.json",
    )

    def __init__(self, workspace_repo: WorkspaceRepository, file_repo: FileCollectionRepository, enrich_queue_repo: FileEnrichQueueRepository, body_repo: FileBodyRepository, lsp_repo: LspToolDataRepository, readiness_repo: ToolReadinessRepository, policy: CollectionPolicyDTO, lsp_backend: LspExtractionBackend, policy_repo: PipelinePolicyRepository | None=None, event_repo: PipelineJobEventRepository | None=None, error_event_repo: PipelineErrorEventRepository | None=None, candidate_index_sink: CandidateIndexSink | None=None, vector_index_sink: VectorIndexSink | None=None, run_mode: str='dev', parent_alive_probe: Callable[[], bool] | None=None, persist_body_for_read: bool=True, repo_registry_repo: RepoRegistryRepository | None=None, l3_parallel_enabled: bool=True, l3_executor_max_workers: int=0, l3_recent_success_ttl_sec: int=120, l3_backpressure_on_interactive: bool=True, l3_backpressure_cooldown_ms: int=300, l3_supported_languages: tuple[str, ...] | None=None, lsp_probe_bootstrap_file_window: int=256, lsp_probe_bootstrap_top_k: int=3, lsp_probe_language_priority: tuple[str, ...]=("go:1.5", "java:1.4", "kotlin:1.3"), lsp_probe_l1_languages: tuple[str, ...]=("go", "java", "kotlin")) -> None:
        self._workspace_repo = workspace_repo
        self._file_repo = file_repo
        self._enrich_queue_repo = enrich_queue_repo
        self._body_repo = body_repo
        self._lsp_repo = lsp_repo
        self._readiness_repo = readiness_repo
        self._policy = policy
        self._lsp_backend = lsp_backend
        self._policy_repo = policy_repo
        self._event_repo = event_repo
        self._error_event_repo = error_event_repo
        self._candidate_index_sink = candidate_index_sink
        self._vector_index_sink = vector_index_sink
        self._repo_registry_repo = repo_registry_repo
        self._run_mode = 'prod' if run_mode == 'prod' else 'dev'
        self._parent_alive_probe = parent_alive_probe
        self._persist_body_for_read = persist_body_for_read
        self._l3_parallel_enabled = bool(l3_parallel_enabled)
        self._lsp_probe_bootstrap_file_window = max(1, int(lsp_probe_bootstrap_file_window))
        self._lsp_probe_bootstrap_top_k = max(1, int(lsp_probe_bootstrap_top_k))
        self._lsp_probe_language_priority_weights = _parse_language_priority_weights(lsp_probe_language_priority)
        self._lsp_probe_l1_languages = tuple(item.strip() for item in lsp_probe_l1_languages if item.strip() != "")
        if l3_supported_languages is None:
            self._l3_supported_languages = tuple(item.strip() for item in get_enabled_language_names() if item.strip() != "")
        else:
            self._l3_supported_languages = tuple(item.strip() for item in l3_supported_languages if item.strip() != "")
        self._watcher_queue_max = self.WATCHER_QUEUE_MAX
        self._watcher_overflow_rescan_cooldown_sec = self.WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC
        if self._policy_repo is not None:
            try:
                runtime_policy = self._policy_repo.get_policy()
                self._watcher_queue_max = max(100, int(runtime_policy.watcher_queue_max))
                self._watcher_overflow_rescan_cooldown_sec = max(
                    1, int(runtime_policy.watcher_overflow_rescan_cooldown_sec)
                )
            except (RuntimeError, ValueError):
                # 정책 조회 실패 시 기본 안전값으로 동작한다.
                self._watcher_queue_max = self.WATCHER_QUEUE_MAX
                self._watcher_overflow_rescan_cooldown_sec = self.WATCHER_OVERFLOW_RESCAN_COOLDOWN_SEC
        self._stop_event = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._enrich_threads: list[threading.Thread] = []
        self._watcher_thread: threading.Thread | None = None
        self._event_queue: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=self._watcher_queue_max)
        self._l3_ready_queue: queue.Queue[FileEnrichJobDTO] = queue.Queue()
        self._watcher_debounce_ms = 300
        self._debounce_events: dict[tuple[str, str], tuple[float, str, str]] = {}
        self._debounce_lock = threading.Lock()
        self._watcher_drop_count = 0
        self._watcher_overflow_count = 0
        self._watcher_last_overflow_at: str | None = None
        self._enrich_latency_samples_ms: list[float] = []
        self._throughput_samples_jobs_per_sec: list[float] = []
        self._throughput_ema_jobs_per_sec = 0.0
        self._throughput_alpha = 0.2
        self._metrics_lock = threading.Lock()
        self._worker_state = 'running'
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._last_error_at: str | None = None
        self._indexing_mode = 'steady'
        self._repo_support = CollectionRepoSupport(
            workspace_repo=self._workspace_repo,
            policy=self._policy,
            policy_repo=self._policy_repo,
            lsp_backend=self._lsp_backend,
            repo_registry_repo=self._repo_registry_repo,
            lsp_prewarm_min_language_files=self.LSP_PREWARM_MIN_LANGUAGE_FILES,
            lsp_prewarm_top_language_count=self.LSP_PREWARM_TOP_LANGUAGE_COUNT,
        )
        self._fanout_resolver = WorkspaceFanoutResolver(
            workspace_repo=self._workspace_repo,
            load_gitignore_spec=self._repo_support.load_gitignore_spec,
            is_collectible=self._repo_support.is_collectible,
            build_markers=self.WORKSPACE_SCAN_BUILD_MARKERS,
        )
        self._scanner = FileScanner(
            file_repo=self._file_repo,
            enrich_queue_repo=self._enrich_queue_repo,
            candidate_index_sink=self._candidate_index_sink,
            resolve_lsp_language=self._repo_support.resolve_lsp_language,
            configure_lsp_prewarm_languages=self._repo_support.configure_lsp_prewarm_languages,
            schedule_lsp_probe_for_file=self._repo_support.schedule_lsp_probe_for_file,
            resolve_repo_identity=self._repo_support.resolve_repo_identity,
            load_gitignore_spec=self._repo_support.load_gitignore_spec,
            is_collectible=self._repo_support.is_collectible,
            priority_low=self.PRIORITY_LOW,
            priority_medium=self.PRIORITY_MEDIUM,
            scan_flush_batch_size=self.SCAN_FLUSH_BATCH_SIZE,
            scan_flush_interval_sec=self.SCAN_FLUSH_INTERVAL_SEC,
            scan_hash_max_workers=self.SCAN_HASH_MAX_WORKERS,
            bootstrap_file_window=self._lsp_probe_bootstrap_file_window,
            bootstrap_top_k=self._lsp_probe_bootstrap_top_k,
            language_priority_weights=self._lsp_probe_language_priority_weights,
        )
        self._error_policy = CollectionErrorPolicy(
            error_event_repo=self._error_event_repo,
            run_mode=self._run_mode,
            stop_background=self._stop_event.set,
        )
        self._enrich_engine = EnrichEngine(
            file_repo=self._file_repo,
            enrich_queue_repo=self._enrich_queue_repo,
            body_repo=self._body_repo,
            lsp_repo=self._lsp_repo,
            readiness_repo=self._readiness_repo,
            policy=self._policy,
            lsp_backend=self._lsp_backend,
            policy_repo=self._policy_repo,
            event_repo=self._event_repo,
            vector_index_sink=self._vector_index_sink,
            run_mode=self._run_mode,
            persist_body_for_read=self._persist_body_for_read,
            l3_ready_queue=self._l3_ready_queue,
            error_policy=self._error_policy,
            record_enrich_latency=self._record_enrich_latency,
            assert_parent_alive=self._assert_parent_alive,
            flush_batch_size=self.ENRICH_FLUSH_BATCH_SIZE,
            flush_interval_sec=self.ENRICH_FLUSH_INTERVAL_SEC,
            flush_max_body_bytes=self.ENRICH_FLUSH_MAX_BODY_BYTES,
            l3_parallel_enabled=self._l3_parallel_enabled,
            l3_executor_max_workers=l3_executor_max_workers,
            l3_recent_success_ttl_sec=l3_recent_success_ttl_sec,
            l3_backpressure_on_interactive=l3_backpressure_on_interactive,
            l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms,
            l3_supported_languages=self._l3_supported_languages,
            lsp_probe_l1_languages=self._lsp_probe_l1_languages,
        )
        self._pipeline_worker = PipelineWorker(
            process_enrich_jobs=self._enrich_engine.process_enrich_jobs,
            process_enrich_jobs_l2=self._enrich_engine.process_enrich_jobs_l2,
            process_enrich_jobs_l3=self._enrich_engine.process_enrich_jobs_l3,
        )
        self._runtime_manager = RuntimeManager(
            stop_event=self._stop_event,
            enrich_queue_repo=self._enrich_queue_repo,
            workspace_repo=self._workspace_repo,
            policy=self._policy,
            policy_repo=self._policy_repo,
            assert_parent_alive=self._assert_parent_alive,
            scan_once=self.scan_once,
            process_enrich_jobs_bootstrap=self._enrich_engine.process_enrich_jobs_bootstrap,
            handle_background_collection_error=self._handle_background_collection_error_proxy,
            prune_error_events_if_needed=self._error_policy.prune_error_events_if_needed,
            watcher_loop=self._watcher_loop,
        )
        self._watcher = EventWatcher(
            workspace_repo=self._workspace_repo,
            file_repo=self._file_repo,
            candidate_index_sink=self._candidate_index_sink,
            event_queue=self._event_queue,
            stop_event=self._stop_event,
            debounce_events=self._debounce_events,
            debounce_lock=self._debounce_lock,
            watcher_debounce_ms=lambda: self._watcher_debounce_ms,
            assert_parent_alive=self._assert_parent_alive,
            index_file_with_priority=self._index_file_with_priority,
            handle_background_collection_error=self._handle_background_collection_error_proxy,
            priority_high=self.PRIORITY_HIGH,
            set_observer=self._runtime_manager.set_observer,
            watcher_overflow_rescan_cooldown_sec=self._watcher_overflow_rescan_cooldown_sec,
            now_monotonic=time.monotonic,
            on_watcher_queue_overflow=self._record_watcher_queue_overflow,
            schedule_rescan=self._schedule_rescan_from_watcher,
            on_watcher_file_race=self._record_watcher_file_race,
        )
        self._metrics_service = PipelineMetricsService(
            refresh_indexing_mode=self._enrich_engine.refresh_indexing_mode,
            enrich_queue_repo=self._enrich_queue_repo,
            file_repo=self._file_repo,
            l3_queue_size=lambda: self._l3_ready_queue.qsize(),
            metrics_lock=self._metrics_lock,
            enrich_latency_samples_ms=self._enrich_latency_samples_ms,
            throughput_samples_jobs_per_sec=self._throughput_samples_jobs_per_sec,
            get_throughput_ema=lambda: self._throughput_ema_jobs_per_sec,
            set_throughput_ema=self._set_throughput_ema_jobs_per_sec,
            throughput_alpha=self._throughput_alpha,
            enrich_threads_count=self._runtime_manager.enrich_thread_count,
            compute_coverage_bps=self._enrich_engine.compute_coverage_bps,
            indexing_mode=self._enrich_engine.indexing_mode,
            worker_state=lambda: self._worker_state,
            last_error_code=self._error_policy.last_error_code,
            last_error_message=self._error_policy.last_error_message,
            last_error_at=self._error_policy.last_error_at,
            watcher_queue_depth=lambda: self._event_queue.qsize(),
            watcher_drop_count=self._watcher_drop_count_snapshot,
            watcher_overflow_count=self._watcher_overflow_count_snapshot,
            watcher_last_overflow_at=self._watcher_last_overflow_at_snapshot,
            lsp_metrics_snapshot=self._lsp_runtime_metrics_snapshot,
        )

    def scan_once(self, repo_root: str) -> CollectionScanResultDTO:
        """L1 스캔을 실행한다. workspace 컨테이너는 top-level repo fan-out을 수행한다."""
        root_path = Path(repo_root).expanduser().resolve()
        fanout_targets = self._fanout_resolver.resolve_targets(root_path)
        if len(fanout_targets) == 0:
            return self._scanner.scan_once(str(root_path))
        return self._scan_workspace_fanout(root_path=root_path, targets=fanout_targets)

    def _scan_workspace_fanout(self, root_path: Path, targets: list[Path]) -> CollectionScanResultDTO:
        """workspace 컨테이너 하위 repo를 top-level 단위로 순차 스캔한다."""
        scanned_total = 0
        indexed_total = 0
        deleted_total = 0
        succeeded = 0
        failed = 0
        results: list[CollectionScanRepoResultDTO] = []
        for target in targets:
            try:
                scan_result = self._scanner.scan_once(str(target))
                scanned_total += scan_result.scanned_count
                indexed_total += scan_result.indexed_count
                deleted_total += scan_result.deleted_count
                succeeded += 1
                results.append(
                    CollectionScanRepoResultDTO(
                        repo_root=str(target),
                        scanned_count=scan_result.scanned_count,
                        indexed_count=scan_result.indexed_count,
                        deleted_count=scan_result.deleted_count,
                    )
                )
            except CollectionError as exc:
                failed += 1
                results.append(
                    CollectionScanRepoResultDTO(
                        repo_root=str(target),
                        scanned_count=0,
                        indexed_count=0,
                        deleted_count=0,
                        status="error",
                        error_code=exc.context.code,
                        error_message=exc.context.message,
                    )
                )
        return CollectionScanResultDTO(
            scanned_count=scanned_total,
            indexed_count=indexed_total,
            deleted_count=deleted_total,
            mode="fanout_top_level",
            target_repo_count=len(targets),
            succeeded_repo_count=succeeded,
            failed_repo_count=failed,
            repo_results=tuple(results),
        )

    def index_file(self, repo_root: str, relative_path: str) -> CollectionScanResultDTO:
        """단일 파일 인덱싱을 전용 스캐너 컴포넌트로 위임한다."""
        return self._scanner.index_file(repo_root, relative_path)

    def process_enrich_jobs(self, limit: int) -> int:
        """L2/L3 통합 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs(limit)

    def process_enrich_jobs_l2(self, limit: int) -> int:
        """L2 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs_l2(limit)

    def process_enrich_jobs_l3(self, limit: int) -> int:
        """L3 보강 처리를 전용 워커 컴포넌트로 위임한다."""
        return self._pipeline_worker.process_enrich_jobs_l3(limit)

    def _watcher_loop(self) -> None:
        """watcher 루프를 전용 이벤트 컴포넌트로 위임한다."""
        try:
            self._watcher.watcher_loop()
        except CollectionError as exc:
            if self._handle_background_collection_error_proxy(exc=exc, phase="watcher_loop", worker_name="watcher"):
                return
        except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
            wrapped = CollectionError(
                ErrorContext(
                    code="ERR_WATCHER_RUNTIME_FAILED",
                    message=f"watcher 루프 실패: {exc}",
                )
            )
            if self._handle_background_collection_error_proxy(exc=wrapped, phase="watcher_loop", worker_name="watcher"):
                return

    def _handle_fs_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """파일 시스템 이벤트 처리를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.handle_fs_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def _push_debounced_event(self, event_type: str, src_path: str, dest_path: str) -> None:
        """디바운스 이벤트 적재를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.push_debounced_event(event_type=event_type, src_path=src_path, dest_path=dest_path)

    def _flush_debounced_events(self) -> None:
        """디바운스 이벤트 flush를 전용 이벤트 컴포넌트로 위임한다."""
        self._watcher.flush_debounced_events()

    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        """파이프라인 메트릭 계산을 전용 메트릭 컴포넌트로 위임한다."""
        return self._metrics_service.get_pipeline_metrics()

    def _record_enrich_latency(self, latency_ms: float) -> None:
        """처리 지연시간 기록을 전용 메트릭 컴포넌트로 위임한다."""
        self._metrics_service.record_enrich_latency(latency_ms)

    def _set_throughput_ema_jobs_per_sec(self, value: float) -> None:
        """처리량 EMA 값을 명시적으로 갱신한다."""
        self._throughput_ema_jobs_per_sec = value

    def _watcher_drop_count_snapshot(self) -> int:
        """watcher drop 카운트 스냅샷을 반환한다."""
        with self._metrics_lock:
            return int(self._watcher_drop_count)

    def _watcher_overflow_count_snapshot(self) -> int:
        """watcher overflow 카운트 스냅샷을 반환한다."""
        with self._metrics_lock:
            return int(self._watcher_overflow_count)

    def _watcher_last_overflow_at_snapshot(self) -> str | None:
        """watcher 마지막 overflow 시각을 반환한다."""
        with self._metrics_lock:
            return self._watcher_last_overflow_at

    def _lsp_runtime_metrics_snapshot(self) -> dict[str, int]:
        """LSP 런타임 메트릭 스냅샷을 반환한다."""
        if hasattr(self._lsp_backend, "get_runtime_metrics"):
            try:
                metrics = getattr(self._lsp_backend, "get_runtime_metrics")()
                if isinstance(metrics, dict):
                    return {str(key): int(value) for key, value in metrics.items()}
            except (RuntimeError, OSError, ValueError, TypeError):
                return {}
        return {}

    def _record_watcher_queue_overflow(self, repo_root: str | None, src_path: str) -> None:
        """watcher 큐 overflow를 기록한다."""
        now_iso = now_iso8601_utc()
        with self._metrics_lock:
            self._watcher_drop_count += 1
            self._watcher_overflow_count += 1
            self._watcher_last_overflow_at = now_iso
        self._error_policy.record_error_event(
            component="event_watcher",
            phase="watcher_overflow",
            severity="error",
            error_code="ERR_WATCHER_QUEUE_OVERFLOW",
            error_message="watcher queue overflow detected; recovery rescan scheduled",
            error_type="QueueFull",
            repo_root=repo_root,
            relative_path=src_path,
            job_id=None,
            attempt_count=0,
            context_data={"queue_max": self._watcher_queue_max},
            worker_name="watcher",
        )

    def _schedule_rescan_from_watcher(self, repo_root: str) -> None:
        """watcher overflow 복구를 위해 단일 repo 재스캔을 실행한다."""
        _ = self._scanner.scan_once(repo_root)

    def _record_watcher_file_race(self, repo_root: str, relative_path: str, reason: str) -> None:
        """watcher 경합성 파일 누락 이벤트를 저심각도 경고로 기록한다."""
        self._error_policy.record_error_event(
            component="event_watcher",
            phase="watcher_file_race",
            severity="warning",
            error_code="ERR_WATCHER_FILE_RACE",
            error_message="watcher 이벤트 처리 중 파일이 사라졌습니다",
            error_type="FileRaceCondition",
            repo_root=repo_root,
            relative_path=relative_path,
            job_id=None,
            attempt_count=0,
            context_data={"reason": reason},
            worker_name="watcher",
        )

    def list_files(self, repo_root: str, limit: int, prefix: str | None) -> list[dict[str, object]]:
        if limit <= 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_LIMIT', message='limit는 1 이상이어야 합니다'))
        rows = self._file_repo.list_files(repo_root=repo_root, limit=limit, prefix=prefix)
        return [{'repo': item.repo, 'relative_path': item.relative_path, 'size_bytes': item.size_bytes, 'mtime_ns': item.mtime_ns, 'content_hash': item.content_hash, 'enrich_state': item.enrich_state} for item in rows]

    def read_file(self, repo_root: str, relative_path: str, offset: int, limit: int | None) -> FileReadResultDTO:
        if offset < 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_OFFSET', message='offset은 0 이상이어야 합니다'))
        if limit is not None and limit <= 0:
            raise CollectionError(ErrorContext(code='ERR_INVALID_LIMIT', message='limit는 1 이상이어야 합니다'))
        row = self._file_repo.get_file(repo_root=repo_root, relative_path=relative_path)
        if row is None or row.is_deleted:
            raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='파일 메타데이터를 찾을 수 없습니다'))
        try:
            body_text = self._body_repo.read_body_text(repo_root=repo_root, relative_path=relative_path, content_hash=row.content_hash)
        except FileBodyDecodeError as exc:
            self._error_policy.record_error_event(component='file_collection_service', phase='read_file', severity='error', error_code='ERR_L2_BODY_CORRUPT', error_message=str(exc), error_type=type(exc).__name__, repo_root=repo_root, relative_path=relative_path, job_id=None, attempt_count=0, context_data={'content_hash': row.content_hash}, worker_name='http_read', stacktrace_text=traceback.format_exc())
            raise CollectionError(ErrorContext(code='ERR_L2_BODY_CORRUPT', message='L2 본문 데이터가 손상되어 읽을 수 없습니다')) from exc
        source = 'l2'
        if body_text is None:
            source = 'fs'
            file_path = Path(row.absolute_path)
            if not file_path.exists() or not file_path.is_file():
                raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='파일 시스템에서 파일을 찾을 수 없습니다'))
            decoded = decode_bytes_with_policy(file_path.read_bytes())
            body_text = decoded.text
        lines = body_text.splitlines()
        total_lines = len(lines)
        end_index = total_lines if limit is None else min(total_lines, offset + limit)
        sliced = lines[offset:end_index]
        next_offset = end_index if end_index < total_lines else None
        return FileReadResultDTO(relative_path=relative_path, content='\n'.join(sliced), start_line=offset + 1, end_line=end_index, source=source, total_lines=total_lines, is_truncated=next_offset is not None, next_offset=next_offset)

    def _rebalance_jobs_by_language(self, jobs: list[FileEnrichJobDTO]) -> list[FileEnrichJobDTO]:
        return self._enrich_engine._rebalance_jobs_by_language(jobs)

    def start_background(self) -> None:
        self._enrich_engine.reset_runtime_state()
        self._worker_state = 'running'
        self._runtime_manager.start_background()

    def stop_background(self) -> None:
        self._runtime_manager.stop_background()
        self._enrich_engine.shutdown()
        self._repo_support.shutdown_probe_executor()

    def reset_probe_state(self) -> None:
        """성능 측정/진단용으로 probe 상태를 초기화한다."""
        resetter = getattr(self._lsp_backend, "reset_probe_state", None)
        if callable(resetter):
            resetter()

    def reset_lsp_runtime(self) -> None:
        """성능 측정/진단용으로 LSP 런타임을 종료한다."""
        resetter = getattr(self._lsp_backend, "reset_lsp_runtime", None)
        if callable(resetter):
            resetter()

    def reset_lsp_unavailable_cache(self, repo_root: str | None = None, language: str | None = None) -> int:
        """LSP unavailable 캐시를 수동 초기화한다."""
        resetter = getattr(self._lsp_backend, "clear_unavailable_state", None)
        if not callable(resetter):
            return 0
        return int(resetter(repo_root=repo_root, language=language))

    def reset_runtime_state(self) -> None:
        """성능 측정/진단용으로 인메모리 런타임 상태를 초기화한다."""
        self._enrich_engine.reset_runtime_state()

    @contextmanager
    def temporary_scan_exclude_globs(self, globs: tuple[str, ...]):
        """scan_once 동안만 추가 exclude globs를 적용한다 (perf 측정 전용)."""
        manager = getattr(self._repo_support, "temporary_extra_exclude_globs", None)
        if callable(manager):
            with manager(globs):
                yield
            return
        yield

    def list_error_events(self, limit: int, offset: int=0, repo_root: str | None=None, error_code: str | None=None) -> list[dict[str, object]]:
        if self._error_event_repo is None:
            return []
        items = self._error_event_repo.list_events(limit=limit, offset=offset, repo_root=repo_root, error_code=error_code)
        return [item.to_dict() for item in items]

    def get_error_event(self, event_id: str) -> dict[str, object] | None:
        if self._error_event_repo is None:
            return None
        item = self._error_event_repo.get_event(event_id=event_id)
        if item is None:
            return None
        return item.to_dict()

    def _is_deletion_hold_enabled(self) -> bool:
        return self._repo_support.is_deletion_hold_enabled()

    def _record_error_event(self, component: str, phase: str, severity: str, error_code: str, error_message: str, error_type: str, repo_root: str | None, relative_path: str | None, job_id: str | None, attempt_count: int, context_data: dict[str, object], worker_name: str='collection', stacktrace_text: str | None=None) -> None:
        self._error_policy.record_error_event(component=component, phase=phase, severity=severity, error_code=error_code, error_message=error_message, error_type=error_type, repo_root=repo_root, relative_path=relative_path, job_id=job_id, attempt_count=attempt_count, context_data=context_data, worker_name=worker_name, stacktrace_text=stacktrace_text)

    def _index_file_with_priority(self, repo_root: str, relative_path: str, priority: int, enqueue_source: str) -> None:
        if relative_path.strip() == '':
            raise CollectionError(ErrorContext(code='ERR_RELATIVE_PATH_REQUIRED', message='relative_path는 필수입니다'))
        root = Path(repo_root).expanduser().resolve()
        repo_identity = self._repo_support.resolve_repo_identity(str(root))
        file_path = (root / relative_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            raise CollectionError(ErrorContext(code='ERR_FILE_NOT_FOUND', message='대상 파일을 찾을 수 없습니다'))
        gitignore_spec = self._repo_support.load_gitignore_spec(root)
        if not self._repo_support.is_collectible(file_path=file_path, repo_root=root, gitignore_spec=gitignore_spec):
            # watcher 이벤트에서 정책 비대상 파일은 큐에 적재하지 않는다.
            return
        now_iso = now_iso8601_utc()
        content_bytes = file_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        l1_row = CollectedFileL1DTO(repo_id=repo_identity.repo_id, repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), absolute_path=str(file_path), repo_label=repo_identity.repo_label, mtime_ns=file_path.stat().st_mtime_ns, size_bytes=file_path.stat().st_size, content_hash=content_hash, is_deleted=False, last_seen_at=now_iso, updated_at=now_iso, enrich_state='PENDING')
        self._file_repo.upsert_file(l1_row)
        self._enrich_queue_repo.enqueue(repo_id=repo_identity.repo_id, repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), content_hash=content_hash, priority=priority, enqueue_source=enqueue_source, now_iso=now_iso)
        if self._candidate_index_sink is not None:
            self._candidate_index_sink.record_upsert(CandidateIndexChangeDTO(repo_id=repo_identity.repo_id, repo_root=str(root), relative_path=str(file_path.relative_to(root).as_posix()), absolute_path=str(file_path), content_hash=content_hash, mtime_ns=file_path.stat().st_mtime_ns, size_bytes=file_path.stat().st_size, event_source=enqueue_source, recorded_at=now_iso))

    def _assert_parent_alive(self, worker_name: str) -> None:
        if self._parent_alive_probe is None:
            return
        if self._parent_alive_probe():
            return
        raise CollectionError(ErrorContext(code='ERR_ORPHAN_DETECTED', message=f'고아 워커 감지: {worker_name}'))

    def _handle_background_collection_error_proxy(self, exc: CollectionError, phase: str, worker_name: str) -> bool:
        """오류 정책 컴포넌트로 background 오류 처리를 위임한다."""
        should_stop = self._error_policy.handle_background_collection_error(exc=exc, phase=phase, worker_name=worker_name)
        if should_stop:
            self._worker_state = 'failed'
        return should_stop

    def _prune_error_events_if_needed(self) -> None:
        self._error_policy.prune_error_events_if_needed()

def build_default_file_collection_service(workspace_repo: WorkspaceRepository, file_repo: FileCollectionRepository, enrich_queue_repo: FileEnrichQueueRepository, body_repo: FileBodyRepository, lsp_repo: LspToolDataRepository, readiness_repo: ToolReadinessRepository, policy_repo: PipelinePolicyRepository | None=None, event_repo: PipelineJobEventRepository | None=None, error_event_repo: PipelineErrorEventRepository | None=None, candidate_index_sink: CandidateIndexSink | None=None, vector_index_sink: VectorIndexSink | None=None, retry_max_attempts: int=5, retry_backoff_base_sec: int=1, queue_poll_interval_ms: int=500, include_ext: tuple[str, ...] | None=None, exclude_globs: tuple[str, ...] | None=None, watcher_debounce_ms: int=300, run_mode: str='dev', parent_alive_probe: Callable[[], bool] | None=None, lsp_backend: LspExtractionBackend | None=None, persist_body_for_read: bool=True, l3_parallel_enabled: bool=True, l3_executor_max_workers: int=0, l3_recent_success_ttl_sec: int=120, l3_backpressure_on_interactive: bool=True, l3_backpressure_cooldown_ms: int=300, l3_supported_languages: tuple[str, ...] | None=None, lsp_probe_bootstrap_file_window: int=256, lsp_probe_bootstrap_top_k: int=3, lsp_probe_language_priority: tuple[str, ...]=("go:1.5", "java:1.4", "kotlin:1.3"), lsp_probe_l1_languages: tuple[str, ...]=("go", "java", "kotlin")) -> CollectionRuntimePort:
    resolved_include_ext = include_ext if include_ext is not None else get_default_collection_extensions()
    resolved_exclude_globs = exclude_globs if exclude_globs is not None else DEFAULT_COLLECTION_EXCLUDE_GLOBS
    policy = CollectionPolicyDTO(include_ext=resolved_include_ext, exclude_globs=resolved_exclude_globs, max_file_size_bytes=512 * 1024, scan_interval_sec=180, max_enrich_batch=20, retry_max_attempts=retry_max_attempts, retry_backoff_base_sec=retry_backoff_base_sec, queue_poll_interval_ms=queue_poll_interval_ms)
    resolved_lsp_backend = lsp_backend if lsp_backend is not None else SolidLspExtractionBackend(LspHub())
    repo_registry_repo = RepoRegistryRepository(file_repo.db_path)
    service = FileCollectionService(workspace_repo=workspace_repo, file_repo=file_repo, enrich_queue_repo=enrich_queue_repo, body_repo=body_repo, lsp_repo=lsp_repo, readiness_repo=readiness_repo, policy=policy, lsp_backend=resolved_lsp_backend, policy_repo=policy_repo, event_repo=event_repo, error_event_repo=error_event_repo, candidate_index_sink=candidate_index_sink, vector_index_sink=vector_index_sink, run_mode=run_mode, parent_alive_probe=parent_alive_probe, persist_body_for_read=persist_body_for_read, repo_registry_repo=repo_registry_repo, l3_parallel_enabled=l3_parallel_enabled, l3_executor_max_workers=l3_executor_max_workers, l3_recent_success_ttl_sec=l3_recent_success_ttl_sec, l3_backpressure_on_interactive=l3_backpressure_on_interactive, l3_backpressure_cooldown_ms=l3_backpressure_cooldown_ms, l3_supported_languages=l3_supported_languages, lsp_probe_bootstrap_file_window=lsp_probe_bootstrap_file_window, lsp_probe_bootstrap_top_k=lsp_probe_bootstrap_top_k, lsp_probe_language_priority=lsp_probe_language_priority, lsp_probe_l1_languages=lsp_probe_l1_languages)
    service._watcher_debounce_ms = max(50, watcher_debounce_ms)
    return service


def _extract_error_code_from_message(message: str) -> str:
    trimmed = message.strip()
    if trimmed.startswith("ERR_"):
        return trimmed.split(":", 1)[0].strip()
    lowered = trimmed.lower()
    if _is_workspace_mismatch_error(trimmed):
        return "ERR_LSP_WORKSPACE_MISMATCH"
    if "lsp 서버 실행 파일을 찾을 수 없습니다" in lowered or "command not found" in lowered or "no such file or directory" in lowered:
        return "ERR_LSP_SERVER_MISSING"
    if "스폰 실패" in lowered or "permission denied" in lowered:
        return "ERR_LSP_SERVER_SPAWN_FAILED"
    if "기동 대기 시간이 초과" in lowered:
        return "ERR_LSP_START_TIMEOUT"
    if "runtime" in lowered and "mismatch" in lowered:
        return "ERR_RUNTIME_MISMATCH"
    if "broken pipe" in lowered:
        return "ERR_BROKEN_PIPE"
    if "timed out" in lowered or "timeout" in lowered:
        return "ERR_RPC_TIMEOUT"
    if "server exited" in lowered:
        return "ERR_SERVER_EXITED"
    return "ERR_LSP_PROBE_FAILED"


def _is_warming_probe_error(code: str, message: str) -> bool:
    if code == "ERR_LSP_INDEXING_WARMING":
        return True
    lowered = message.lower()
    return ("indexing" in lowered) or ("workspace loading" in lowered) or ("timeout" in lowered)


def _is_unavailable_probe_error(code: str) -> bool:
    return code in {
        "ERR_LSP_SERVER_MISSING",
        "ERR_LSP_SERVER_SPAWN_FAILED",
        "ERR_CONFIG_INVALID",
        "ERR_RUNTIME_MISMATCH",
        "ERR_LSP_START_TIMEOUT",
        "ERR_RPC_TIMEOUT",
        "ERR_LSP_INTERACTIVE_TIMEOUT",
    }


def _is_workspace_mismatch_error(message: str) -> bool:
    lowered = message.lower()
    return "workspace contains" in lowered and "no " in lowered and "contains" in lowered


def _next_transient_backoff_sec(fail_count: int) -> float:
    if fail_count <= 1:
        return 5.0
    if fail_count == 2:
        return 15.0
    if fail_count == 3:
        return 30.0
    return 60.0


def _parse_language_priority_weights(items: tuple[str, ...]) -> dict[Language, float]:
    """언어 우선순위 설정 문자열을 가중치 맵으로 파싱한다."""
    weights: dict[Language, float] = {}
    for item in items:
        raw = item.strip()
        if raw == "" or ":" not in raw:
            continue
        name, raw_weight = raw.split(":", 1)
        language = resolve_language_from_path(file_path=f"file.{name.strip().lower()}")
        if language is None:
            continue
        try:
            weight = max(0.1, float(raw_weight.strip()))
        except ValueError:
            continue
        weights[language] = weight
    return weights
