from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

from sari.core.exceptions import DaemonError, ValidationError
from sari.core.language.registry import resolve_language_from_path
from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.lsp.document_symbols import request_document_symbols_with_optional_sync
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_location_to_repo_relative, normalize_repo_relative_path
from sari.services.collection.concurrency.interpreter_pool import (
    create_interpreter_pool_executor,
    normalize_executor_mode,
    parse_non_negative_int,
    parse_positive_int,
)
from sari.services.collection.l5.lsp.scope_planner import LspScopePlanner
from sari.services.collection.l5.lsp.runtime_metrics_builder import build_runtime_metrics
from sari.services.collection.l5.lsp.probe_state_update_service import LspProbeStateUpdateService
from sari.services.collection.l5.lsp.broker_guard_service import LspBrokerGuardService
from sari.services.collection.l5.lsp.runtime_mismatch_recovery_service import LspRuntimeMismatchRecoveryService
from sari.services.collection.l5.lsp.scope_runtime_service import LspScopeRuntimeService
from sari.services.collection.l5.lsp.extract_error_mapper import LspExtractErrorMapper
from sari.services.collection.l5.lsp.symbol_normalizer_service import LspSymbolNormalizerService
from sari.services.collection.l5.lsp.extract_request_runner_service import LspExtractRequestRunnerService
from sari.services.collection.l5.lsp.standby_retention_service import LspStandbyRetentionService
from sari.services.collection.l5.lsp.parallelism_service import LspParallelismService
from sari.services.collection.l5.lsp.session_broker import LspSessionBroker
from sari.services.collection.perf_trace import PerfTracer
from sari.services.collection.l1.watcher_hotness_tracker import WatcherHotnessTracker
from sari.services.collection.l5.solid_lsp_probe_mixin import (
    SolidLspProbeMixin,
    _ProbeStateRecord,
    _extract_error_code_from_message,
    _is_unavailable_probe_error,
    _next_transient_backoff_sec,
)
from sari.services.lsp_extraction_contracts import LspExtractionBackend, LspExtractionResultDTO

log = logging.getLogger(__name__)

@dataclass
class _InflightLspExtractState:
    """동일 LSP 추출 요청의 in-flight 상태를 공유한다."""

    event: threading.Event
    result: LspExtractionResultDTO | None


@dataclass
class _ScopeOverrideRecord:
    """성공한 scope escalation 결과를 학습 캐시에 저장한다."""

    scope_root: str
    scope_level: str
    expires_at_monotonic: float
    updated_at_monotonic: float

class SolidLspExtractionBackend(SolidLspProbeMixin):
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
        symbol_normalizer_executor_mode: str = "inline",
        symbol_normalizer_subinterp_workers: int = 2,
        symbol_normalizer_subinterp_min_symbols: int = 200,
        repo_language_probe_repo: RepoLanguageProbeRepository | None = None,
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
        self._repo_language_probe_repo = repo_language_probe_repo
        self._probe_reconcile_clear_count = 0
        self._probe_reconcile_skip_count = 0
        self._probe_state_update_service = LspProbeStateUpdateService(
            resolve_language=lambda relative_path: resolve_language_from_path(file_path=relative_path),
            is_unavailable_probe_error=_is_unavailable_probe_error,
            next_transient_backoff_sec=_next_transient_backoff_sec,
            monotonic_now=time.monotonic,
            probe_unavailable_backoff_initial_sec=self._probe_unavailable_backoff_initial_sec,
            probe_unavailable_backoff_mid_sec=self._probe_unavailable_backoff_mid_sec,
            probe_unavailable_backoff_cap_sec=self._probe_unavailable_backoff_cap_sec,
            probe_timeout_backoff_initial_sec=self._probe_timeout_backoff_initial_sec,
            probe_timeout_backoff_mid_sec=self._probe_timeout_backoff_mid_sec,
            probe_timeout_backoff_cap_sec=self._probe_timeout_backoff_cap_sec,
        )
        self._broker_guard_service = LspBrokerGuardService(
            hub=self._hub,
            perf_tracer=self._perf_tracer,
            get_session_broker=lambda: self._session_broker,
            is_session_broker_enabled=lambda: bool(self._session_broker_enabled),
            get_watcher_hotness_tracker=lambda: self._watcher_hotness_tracker,
            increment_broker_guard_reject=lambda: setattr(
                self,
                "_broker_guard_reject_count",
                int(getattr(self, "_broker_guard_reject_count", 0)) + 1,
            ),
            apply_standby_retention_touch=lambda **kwargs: self._apply_standby_retention_touch(**kwargs),
        )
        self._runtime_mismatch_recovery_service = LspRuntimeMismatchRecoveryService(
            resolve_language=lambda relative_path: resolve_language_from_path(file_path=relative_path),
            monotonic_now=time.monotonic,
        )
        self._scope_override_lock = threading.Lock()
        self._scope_override_ttl_sec = 24 * 60 * 60.0
        self._scope_override_cache: dict[tuple[str, str, str], _ScopeOverrideRecord] = {}
        self._lsp_scope_planner: LspScopePlanner | None = None
        self._lsp_scope_planner_enabled = False
        self._lsp_scope_planner_applied_count = 0
        self._lsp_scope_planner_fallback_index_building_count = 0
        self._scope_override_hit_count = 0
        self._runtime_mismatch_auto_recovered_count = 0
        self._runtime_mismatch_auto_recover_failed_count = 0
        self._runtime_mismatch_restart_cooldown_sec = 2.0
        self._runtime_mismatch_last_restart_at: dict[tuple[str, str], float] = {}
        self._session_broker: LspSessionBroker | None = None
        self._watcher_hotness_tracker: WatcherHotnessTracker | None = None
        self._session_broker_enabled = False
        self._broker_guard_reject_count = 0
        self._broker_parallelism_guard_skip_count = 0
        self._document_symbol_sync_skip_requested_count = 0
        self._document_symbol_sync_skip_accepted_count = 0
        self._document_symbol_sync_skip_legacy_fallback_count = 0
        self._l3_scope_pending_hint_lock = threading.Lock()
        self._l3_scope_pending_hints: dict[tuple[str, str], int] = {}
        self._scope_active_languages: set[str] | None = None
        self._scope_runtime_service = LspScopeRuntimeService(
            get_scope_override=lambda repo_root, relative_path: self.get_scope_override(
                repo_root=repo_root,
                relative_path=relative_path,
            ),
            to_scope_relative_path_or_fallback=lambda **kwargs: self._to_scope_relative_path_or_fallback(**kwargs),
            get_lsp_scope_planner=lambda: self._lsp_scope_planner,
            is_lsp_scope_planner_enabled=lambda: bool(self._lsp_scope_planner_enabled),
            get_scope_active_languages=lambda: self._scope_active_languages,
            perf_tracer=self._perf_tracer,
            on_scope_override_hit=lambda: setattr(self, "_scope_override_hit_count", int(self._scope_override_hit_count) + 1),
            on_scope_planner_applied=lambda: setattr(
                self,
                "_lsp_scope_planner_applied_count",
                int(self._lsp_scope_planner_applied_count) + 1,
            ),
            on_scope_planner_fallback_index_building=lambda: setattr(
                self,
                "_lsp_scope_planner_fallback_index_building_count",
                int(self._lsp_scope_planner_fallback_index_building_count) + 1,
            ),
            l3_scope_pending_hints=self._l3_scope_pending_hints,
            l3_scope_pending_hint_lock=self._l3_scope_pending_hint_lock,
            normalize_repo_relative_path=normalize_repo_relative_path,
        )
        self._extract_error_mapper = LspExtractErrorMapper()
        self._symbol_normalizer_service = LspSymbolNormalizerService(
            normalize_location=lambda **kwargs: normalize_location_to_repo_relative(**kwargs),
            build_symbol_key=lambda **kwargs: self._build_symbol_key(**kwargs),
            resolve_symbol_depth=lambda symbol: self._resolve_symbol_depth(symbol),
            resolve_container_name=lambda symbol: self._resolve_container_name(symbol),
        )
        configured_mode = normalize_executor_mode(str(symbol_normalizer_executor_mode), default="inline")
        configured_workers = parse_positive_int(
            str(max(1, int(symbol_normalizer_subinterp_workers))),
            default=max(1, int(symbol_normalizer_subinterp_workers)),
        )
        configured_min_symbols = parse_non_negative_int(
            str(max(0, int(symbol_normalizer_subinterp_min_symbols))),
            default=max(0, int(symbol_normalizer_subinterp_min_symbols)),
        )
        self._symbol_normalizer_executor_mode = configured_mode
        self._symbol_normalizer_subinterp_workers = configured_workers
        self._symbol_normalizer_subinterp_min_symbols = configured_min_symbols
        self._symbol_normalizer_subinterp_executor: concurrent.futures.Executor | None = None
        if self._symbol_normalizer_executor_mode == "subinterp":
            self._symbol_normalizer_subinterp_executor = create_interpreter_pool_executor(
                max_workers=self._symbol_normalizer_subinterp_workers
            )
            if self._symbol_normalizer_subinterp_executor is None:
                self._symbol_normalizer_executor_mode = "inline"
        self._extract_request_runner_service = LspExtractRequestRunnerService(
            resolve_language=lambda relative_path: self._hub.resolve_language(relative_path),
            resolve_lsp_runtime_scope=lambda **kwargs: self._resolve_lsp_runtime_scope(**kwargs),
            ensure_prewarm=lambda **kwargs: self._ensure_prewarm(**kwargs),
            get_or_start_with_broker_guard=lambda **kwargs: self._get_or_start_with_broker_guard(**kwargs),
            consume_l3_scope_pending_hint=lambda **kwargs: self._consume_l3_scope_pending_hint(**kwargs),
            acquire_l1_probe_slot=lambda: self._acquire_l1_probe_slot(),
            request_document_symbols=lambda lsp, relative_path, sync_with_ls=False: request_document_symbols_with_optional_sync(
                lsp,
                relative_path,
                sync_with_ls=sync_with_ls,
            ),
            perf_tracer=self._perf_tracer,
            increment_doc_sync_requested=lambda: setattr(
                self,
                "_document_symbol_sync_skip_requested_count",
                int(self._document_symbol_sync_skip_requested_count) + 1,
            ),
            increment_doc_sync_accepted=lambda: setattr(
                self,
                "_document_symbol_sync_skip_accepted_count",
                int(self._document_symbol_sync_skip_accepted_count) + 1,
            ),
            increment_doc_sync_legacy_fallback=lambda: setattr(
                self,
                "_document_symbol_sync_skip_legacy_fallback_count",
                int(self._document_symbol_sync_skip_legacy_fallback_count) + 1,
            ),
        )
        self._standby_retention_service = LspStandbyRetentionService(get_hub=lambda: self._hub)
        self._parallelism_service = LspParallelismService(
            hub=self._hub,
            is_profiled_language=lambda language: self._is_profiled_broker_language(language),
            ensure_prewarm=lambda **kwargs: self._ensure_prewarm(**kwargs),
            increment_broker_parallelism_guard_skip=lambda: setattr(
                self,
                "_broker_parallelism_guard_skip_count",
                int(self._broker_parallelism_guard_skip_count) + 1,
            ),
        )

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
                if error_code == "ERR_RUNTIME_MISMATCH":
                    recovered = self._recover_from_runtime_mismatch(
                        repo_root=normalized_repo_root,
                        relative_path=normalized_relative_path,
                    )
                    if recovered:
                        with self._perf_tracer.span(
                            "extract._extract_once_retry_runtime_mismatch",
                            phase="l3_extract",
                            repo_root=normalized_repo_root,
                        ):
                            result = self._extract_once(
                                repo_root=repo_root,
                                normalized_relative_path=normalized_relative_path,
                            )
                        if result.error_message is None:
                            self._runtime_mismatch_auto_recovered_count += 1
                        else:
                            self._runtime_mismatch_auto_recover_failed_count += 1
                    else:
                        self._runtime_mismatch_auto_recover_failed_count += 1
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

    def record_scope_override_success(
        self,
        *,
        repo_root: str,
        relative_path: str,
        scope_root: str,
        scope_level: str,
    ) -> None:
        """성공한 scope를 학습 캐시에 기록한다 (Phase1 baseline)."""
        language = resolve_language_from_path(file_path=relative_path)
        if language is None:
            return
        candidate_dir = self._normalized_scope_candidate_dir(repo_root=repo_root, relative_path=relative_path)
        key = (language.value, str(Path(repo_root).resolve()), candidate_dir)
        now = time.monotonic()
        record = _ScopeOverrideRecord(
            scope_root=str(Path(scope_root).resolve()),
            scope_level=scope_level,
            expires_at_monotonic=now + self._scope_override_ttl_sec,
            updated_at_monotonic=now,
        )
        with self._scope_override_lock:
            self._scope_override_cache[key] = record

    def get_scope_override(
        self,
        *,
        repo_root: str,
        relative_path: str,
    ) -> tuple[str, str] | None:
        """학습된 scope override를 조회한다. (scope_root, scope_level)"""
        language = resolve_language_from_path(file_path=relative_path)
        if language is None:
            return None
        candidate_dir = self._normalized_scope_candidate_dir(repo_root=repo_root, relative_path=relative_path)
        key = (language.value, str(Path(repo_root).resolve()), candidate_dir)
        now = time.monotonic()
        with self._scope_override_lock:
            record = self._scope_override_cache.get(key)
            if record is None:
                return None
            if record.expires_at_monotonic <= now:
                self._scope_override_cache.pop(key, None)
                return None
            return (record.scope_root, record.scope_level)

    def invalidate_scope_override_path(self, *, repo_root: str, relative_path: str) -> int:
        """경로 변경/삭제 이벤트를 위한 scope override 캐시 무효화 (cheap signal 이후 호출)."""
        repo_key = str(Path(repo_root).resolve())
        target = normalize_repo_relative_path(relative_path)
        target_path = Path(target)
        removed: list[tuple[str, str, str]] = []
        with self._scope_override_lock:
            for key in list(self._scope_override_cache.keys()):
                _, cached_repo_root, candidate_dir = key
                if cached_repo_root != repo_key:
                    continue
                candidate_path = Path(candidate_dir)
                if self._paths_overlap(candidate_path, target_path):
                    removed.append(key)
            for key in removed:
                self._scope_override_cache.pop(key, None)
        return len(removed)

    def clear_scope_overrides(self) -> int:
        """테스트/운영 리셋용 scope override 캐시 전체 삭제."""
        with self._scope_override_lock:
            count = len(self._scope_override_cache)
            self._scope_override_cache.clear()
        return count

    def configure_lsp_scope_planner(
        self,
        *,
        planner: LspScopePlanner | None,
        enabled: bool,
    ) -> None:
        """LSP scope planner를 설정한다."""
        self._lsp_scope_planner = planner
        self._lsp_scope_planner_enabled = bool(enabled) and planner is not None

    def configure_session_runtime(
        self,
        *,
        session_broker: LspSessionBroker | None,
        watcher_hotness_tracker: WatcherHotnessTracker | None,
        enabled: bool,
    ) -> None:
        """PR3 baseline: broker/hotness를 backend에 주입한다."""
        self._session_broker = session_broker
        self._watcher_hotness_tracker = watcher_hotness_tracker
        self._session_broker_enabled = bool(enabled) and session_broker is not None
        set_guard = getattr(self._hub, "set_scale_out_guard", None)
        if callable(set_guard):
            if self._session_broker_enabled and session_broker is not None:
                set_guard(lambda language, _repo_root: bool(session_broker.is_profiled_language(language)))
            else:
                set_guard(None)

    def configure_scope_runtime_policy(self, *, active_languages: tuple[str, ...] | None = None) -> None:
        """PR3.3 baseline: planner 실제 적용 언어를 제한한다 (예: java-only)."""
        if active_languages is None:
            self._scope_active_languages = None
            return
        normalized = {str(x).strip().lower() for x in active_languages if str(x).strip()}
        self._scope_active_languages = normalized if normalized else None

    def _normalized_scope_candidate_dir(self, *, repo_root: str, relative_path: str) -> str:
        normalized_relative = normalize_repo_relative_path(relative_path)
        parent = Path(normalized_relative).parent
        if str(parent) in ("", "."):
            return "."
        return str(parent).replace("\\", "/")

    def _paths_overlap(self, candidate: Path, target: Path) -> bool:
        # Path.is_relative_to를 사용해 예외 기반 제어흐름을 피한다.
        return candidate.is_relative_to(target) or target.is_relative_to(candidate)

    def _resolve_lsp_runtime_scope(self, *, repo_root: str, normalized_relative_path: str, language: Language) -> tuple[str, str]:
        return self._scope_runtime_service.resolve_lsp_runtime_scope(
            repo_root=repo_root,
            normalized_relative_path=normalized_relative_path,
            language=language,
        )

    def _to_scope_relative_path_or_fallback(
        self,
        *,
        repo_root: str,
        normalized_relative_path: str,
        runtime_root: str,
        planner: LspScopePlanner | None = None,
    ) -> str:
        repo_root_path = Path(repo_root).resolve()
        runtime_root_path = Path(runtime_root).resolve()
        abs_file_path = (repo_root_path / normalized_relative_path).resolve()
        try:
            abs_file_path.relative_to(runtime_root_path)
        except ValueError:
            return normalized_relative_path

        if planner is None:
            planner = self._lsp_scope_planner
        path_converter = getattr(planner, "to_scope_relative_path", None) if planner is not None else None
        if callable(path_converter):
            try:
                scope_candidate_root = str(runtime_root_path.relative_to(repo_root_path).as_posix())
            except ValueError:
                scope_candidate_root = "."
            return path_converter(
                workspace_relative_path=normalized_relative_path,
                scope_candidate_root=scope_candidate_root,
            )
        try:
            return Path(os.path.relpath(str(abs_file_path), str(runtime_root_path))).as_posix()
        except (ValueError, OSError):
            return normalized_relative_path

    def _resolve_probe_runtime_scope(
        self,
        *,
        repo_root: str,
        sample_relative_path: str,
        language: Language,
    ) -> tuple[str, str]:
        return self._scope_runtime_service.resolve_probe_runtime_scope(
            repo_root=repo_root,
            sample_relative_path=sample_relative_path,
            language=language,
        )

    def _get_or_start_with_broker_guard(
        self,
        *,
        language: Language,
        runtime_scope_root: str,
        lane: str,
        pending_jobs_in_scope: int,
        request_kind: str,
        trace_name: str = "extract_once.get_or_start",
        trace_phase: str = "l3_extract",
    ):
        return self._broker_guard_service.get_or_start_with_broker_guard(
            language=language,
            runtime_scope_root=runtime_scope_root,
            lane=lane,
            pending_jobs_in_scope=pending_jobs_in_scope,
            request_kind=request_kind,
            trace_name=trace_name,
            trace_phase=trace_phase,
        )

    def _apply_standby_retention_touch(
        self,
        *,
        language: Language,
        runtime_scope_root: str,
        lane: str,
        hotness_score: float,
    ) -> None:
        """Phase 1 baseline: topK/cap 기반 warm retention touch/prune를 best-effort로 적용한다."""
        self._standby_retention_service.apply(
            language=language,
            runtime_scope_root=runtime_scope_root,
            lane=lane,
            hotness_score=hotness_score,
            session_broker=self._session_broker,
            session_broker_enabled=bool(self._session_broker_enabled),
        )

    def _is_profiled_broker_language(self, language: Language) -> bool:
        try:
            return self._broker_guard_service.is_profiled_broker_language(language)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            log.debug("is_profiled_language failed for lang=%s", language.value, exc_info=True)
            return False

    def get_l3_group_sort_key(self, *, repo_root: str, sample_relative_path: str, group_size: int) -> tuple[int, int, float, str]:
        """PR3 baseline: L3 group 정렬 힌트 (lane-aware ordering) 를 제공한다.

        반환값은 낮을수록 우선순위가 높다.
        tuple = (tier_rank, active_reuse_rank, negative_hotness_milli, stable_tiebreak)
        """
        del group_size  # Phase 1 baseline: batch affinity는 관측/후속 단계에서 반영
        normalized_relative = normalize_repo_relative_path(sample_relative_path)
        try:
            language = self._hub.resolve_language(normalized_relative)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            log.debug(
                "failed to resolve language for group sort key (repo=%s, path=%s)",
                repo_root,
                normalized_relative,
                exc_info=True,
            )
            return (3, 1, 0.0, f"{repo_root}:{normalized_relative}")
        runtime_scope_root, _runtime_relative = self._resolve_lsp_runtime_scope(
            repo_root=repo_root,
            normalized_relative_path=normalized_relative,
            language=language,
        )
        broker = self._session_broker
        if not self._session_broker_enabled or broker is None or not getattr(broker, "is_profiled_language", lambda _l: False)(language):
            return (2, 1, 0.0, f"{repo_root}:{normalized_relative}")
        hotness = 0.0
        tracker = self._watcher_hotness_tracker
        if tracker is not None:
            try:
                hotness = float(tracker.get_scope_hotness(language=language, lsp_scope_root=runtime_scope_root))
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                hotness = 0.0
        has_active = False
        has_active_scope = getattr(broker, "has_active_scope", None)
        if callable(has_active_scope):
            try:
                has_active = bool(has_active_scope(language=language, lsp_scope_root=runtime_scope_root))
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                has_active = False
        tier_rank = 0 if hotness > 0.0 else 1
        active_reuse_rank = 0 if has_active else 1
        return (tier_rank, active_reuse_rank, -hotness, f"{runtime_scope_root}:{language.value}")

    def prime_l3_group_pending_hints(self, *, group_jobs: list[object]) -> None:
        """Phase 1 tuning: L3 그룹 파일들을 runtime scope별 pending 힌트로 누적한다.

        group_jobs는 FileEnrichJobDTO 리스트를 기대하지만, import 순환 방지를 위해 duck typing으로 처리.
        """
        if not self._session_broker_enabled or self._session_broker is None:
            return
        local_counts: dict[tuple[str, str], int] = {}
        for job in group_jobs:
            repo_root = getattr(job, "repo_root", None)
            relative_path = getattr(job, "relative_path", None)
            if not isinstance(repo_root, str) or not isinstance(relative_path, str):
                continue
            normalized_relative = normalize_repo_relative_path(relative_path)
            try:
                language = self._hub.resolve_language(normalized_relative)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                log.debug(
                    "failed to resolve language while priming pending hints (repo=%s, path=%s)",
                    repo_root,
                    normalized_relative,
                    exc_info=True,
                )
                continue
            if not self._is_profiled_broker_language(language):
                continue
            runtime_scope_root, _runtime_relative = self._resolve_lsp_runtime_scope(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative,
                language=language,
            )
            key = (language.value, runtime_scope_root)
            local_counts[key] = int(local_counts.get(key, 0)) + 1
        if not local_counts:
            return
        with self._l3_scope_pending_hint_lock:
            for key, count in local_counts.items():
                self._l3_scope_pending_hints[key] = max(int(count), int(self._l3_scope_pending_hints.get(key, 0)))

    def _consume_l3_scope_pending_hint(self, *, language: Language, runtime_scope_root: str) -> int:
        return self._scope_runtime_service.consume_l3_scope_pending_hint(
            language=language,
            runtime_scope_root=runtime_scope_root,
        )

    def _extract_once(self, repo_root: str, normalized_relative_path: str) -> LspExtractionResultDTO:
        try:
            run_result = self._extract_request_runner_service.run_request(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative_path,
            )
            lsp = None
            runtime_relative_path = normalized_relative_path
            if isinstance(run_result, tuple) and len(run_result) >= 4:
                language, raw_symbols, lsp, runtime_relative_path = run_result[0], run_result[1], run_result[2], str(run_result[3])
            else:
                language, raw_symbols = run_result  # type: ignore[misc]
        except SolidLSPException as exc:
            return LspExtractionResultDTO(
                symbols=[],
                relations=[],
                error_message=self._extract_error_mapper.map_solid_exception(
                    repo_root=repo_root,
                    normalized_relative_path=normalized_relative_path,
                    exc=exc,
                ),
            )
        except (DaemonError, RuntimeError, OSError, ValueError, TypeError, concurrent.futures.TimeoutError) as exc:
            return LspExtractionResultDTO(
                symbols=[],
                relations=[],
                error_message=self._extract_error_mapper.map_generic_exception(exc),
            )
        with self._perf_tracer.span(
            "extract_once.normalize_symbols",
            phase="l3_extract",
            repo_root=repo_root,
            language=(language.value if "language" in locals() else "unknown"),
        ):
            symbols = self._normalize_symbols(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative_path,
                raw_symbols=raw_symbols,
            )
        relations = self._extract_relations_from_references(
            lsp=lsp,
            runtime_relative_path=runtime_relative_path,
            raw_symbols=raw_symbols,
            normalized_symbols=symbols,
        )
        return LspExtractionResultDTO(symbols=symbols, relations=relations, error_message=None)

    def _extract_relations_from_references(
        self,
        *,
        lsp: object,
        runtime_relative_path: str,
        raw_symbols: list[object],
        normalized_symbols: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """동일 파일 기준 caller -> callee 관계를 references API로 추출한다."""
        request_referencing_symbols = getattr(lsp, "request_referencing_symbols", None)
        if not callable(request_referencing_symbols):
            return []
        reference_pos_by_symbol = self._build_symbol_reference_position_index(raw_symbols=raw_symbols)
        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str, int]] = set()
        for symbol in normalized_symbols:
            target_name = str(symbol.get("name", "")).strip()
            target_kind = str(symbol.get("kind", "")).strip().lower()
            target_line_raw = symbol.get("line", 0)
            if target_name == "":
                continue
            if not self._is_relation_target_kind(target_kind):
                continue
            try:
                target_line = int(target_line_raw)
            except (RuntimeError, ValueError, TypeError):
                continue
            query_line, query_col = reference_pos_by_symbol.get((target_name, target_kind, target_line), (target_line, 0))
            try:
                incoming = request_referencing_symbols(
                    runtime_relative_path,
                    int(query_line),
                    int(query_col),
                    include_imports=False,
                    include_self=False,
                    include_body=False,
                    include_file_symbols=False,
                )
            except (SolidLSPException, RuntimeError, OSError, ValueError, TypeError):
                continue
            if not isinstance(incoming, list):
                continue
            for ref in incoming:
                caller: object | None = None
                line_raw: object = target_line
                if isinstance(ref, dict):
                    caller = ref.get("symbol")
                    line_raw = ref.get("line", target_line)
                elif hasattr(ref, "symbol"):
                    caller = getattr(ref, "symbol", None)
                    line_raw = getattr(ref, "line", target_line)
                else:
                    continue
                caller_name = ""
                caller_location: object | None = None
                if isinstance(caller, dict):
                    caller_name = str(caller.get("name", "")).strip()
                    caller_location = caller.get("location")
                elif caller is not None:
                    caller_name = str(getattr(caller, "name", "")).strip()
                    caller_location = getattr(caller, "location", None)
                if caller_name == "":
                    continue
                caller_relative: object | None = None
                if isinstance(caller_location, dict):
                    caller_relative = caller_location.get("relativePath")
                elif caller_location is not None:
                    caller_relative = getattr(caller_location, "relativePath", None)
                if not isinstance(caller_relative, str) or caller_relative.strip() == "":
                    # same-file 여부를 증명할 수 없는 edge는 저장하지 않는다.
                    continue
                if caller_relative.strip() != runtime_relative_path:
                    # lsp_call_relations 스키마는 파일 단위 replace 계약이라 cross-file edge는 제외한다.
                    continue
                try:
                    relation_line = int(line_raw)
                except (RuntimeError, ValueError, TypeError):
                    relation_line = target_line
                key = (caller_name, target_name, relation_line)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append({"from_symbol": caller_name, "to_symbol": target_name, "line": relation_line})
        return deduped

    def _build_symbol_reference_position_index(
        self, *, raw_symbols: list[object]
    ) -> dict[tuple[str, str, int], tuple[int, int]]:
        """raw 심볼에서 declaration line 키 -> references query(line, column) 인덱스를 생성한다."""
        index: dict[tuple[str, str, int], tuple[int, int]] = {}
        for raw in raw_symbols:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            if name == "":
                continue
            kind = str(raw.get("kind", "")).strip().lower()
            location = raw.get("location")
            if not isinstance(location, dict):
                continue
            selection_range = raw.get("selectionRange")
            if not isinstance(selection_range, dict):
                selection_range = location.get("selectionRange")
            range_data = location.get("range")
            declaration_start = range_data.get("start") if isinstance(range_data, dict) else None
            selection_start = selection_range.get("start") if isinstance(selection_range, dict) else None
            if not isinstance(declaration_start, dict):
                continue
            try:
                # normalize_symbols 키는 declaration line을 유지한다.
                declaration_line = int(declaration_start.get("line", 0))
                query_line = int(
                    selection_start.get("line", declaration_line)
                    if isinstance(selection_start, dict)
                    else declaration_line
                )
                query_col = int(
                    selection_start.get("character", declaration_start.get("character", 0))
                    if isinstance(selection_start, dict)
                    else declaration_start.get("character", 0)
                )
            except (RuntimeError, ValueError, TypeError):
                continue
            index.setdefault((name, kind, declaration_line), (query_line, query_col))
        return index

    def _is_relation_target_kind(self, kind: str) -> bool:
        """relation target으로 취급할 symbol kind를 판정한다."""
        if kind in {"function", "method", "constructor", "class"}:
            return True
        if kind in {"12", "6", "9", "5"}:
            return True
        return False

    def _normalize_symbols(
        self,
        *,
        repo_root: str,
        normalized_relative_path: str,
        raw_symbols: list[object],
    ) -> list[dict[str, object]]:
        if (
            self._symbol_normalizer_executor_mode == "subinterp"
            and self._symbol_normalizer_subinterp_executor is not None
            and len(raw_symbols) >= self._symbol_normalizer_subinterp_min_symbols
        ):
            try:
                future = self._symbol_normalizer_subinterp_executor.submit(
                    _normalize_symbols_subinterp_task,
                    repo_root,
                    normalized_relative_path,
                    raw_symbols,
                )
                result = future.result(timeout=30.0)
                if isinstance(result, list):
                    return result
            except (RuntimeError, OSError, ValueError, TypeError, TimeoutError):
                log.debug(
                    "L5 symbol normalizer subinterpreter path failed; fallback to inline normalization",
                    exc_info=True,
                )
        return self._symbol_normalizer_service.normalize_symbols(
            repo_root=repo_root,
            normalized_relative_path=normalized_relative_path,
            raw_symbols=raw_symbols,
        )

    def shutdown_probe_executor(self) -> None:
        """probe executor와 부가 executor를 함께 종료한다."""
        super().shutdown_probe_executor()
        executor = self._symbol_normalizer_subinterp_executor
        if executor is None:
            return
        try:
            executor.shutdown(wait=True)
        except (RuntimeError, OSError, ValueError, TypeError):
            ...
        finally:
            self._symbol_normalizer_subinterp_executor = None

    def get_parallelism(self, repo_root: str, language: Language) -> int:
        """현재 언어/레포 풀의 병렬 처리 가능 슬롯 수를 반환한다."""
        return self._parallelism_service.get_parallelism(repo_root=repo_root, language=language)

    def get_parallelism_for_batch(self, repo_root: str, language: Language, batch_size: int) -> int:
        """배치 크기를 반영해 풀 인스턴스를 확보하고 병렬도를 반환한다."""
        return self._parallelism_service.get_parallelism_for_batch(
            repo_root=repo_root,
            language=language,
            batch_size=batch_size,
        )

    def set_bulk_mode(self, repo_root: str, language: Language, enabled: bool) -> None:
        """bulk 인덱싱 모드를 LSP 허브에 전달한다."""
        self._parallelism_service.set_bulk_mode(repo_root=repo_root, language=language, enabled=enabled)

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

    def get_runtime_metrics(self) -> dict[str, int]:
        """LSP 허브 런타임 메트릭을 반환한다."""
        with self._probe_lock:
            probe_counts = dict(self._probe_trigger_counts)
            unavailable_count = sum(1 for state in self._probe_state.values() if state.status == "UNAVAILABLE_COOLDOWN")
            workspace_mismatch_count = sum(1 for state in self._probe_state.values() if state.status == "WORKSPACE_MISMATCH")
            cooldown_count = sum(1 for state in self._probe_state.values() if state.status == "COOLDOWN")
            backpressure_cooldown_count = sum(1 for state in self._probe_state.values() if state.status == "BACKPRESSURE_COOLDOWN")
        return build_runtime_metrics(
            hub_metrics=dict(self._hub.get_metrics()),
            probe_trigger_counts=probe_counts,
            scope_planner_applied_count=self._lsp_scope_planner_applied_count,
            scope_planner_fallback_index_building_count=self._lsp_scope_planner_fallback_index_building_count,
            scope_override_hit_count=self._scope_override_hit_count,
            runtime_mismatch_auto_recovered_count=self._runtime_mismatch_auto_recovered_count,
            runtime_mismatch_auto_recover_failed_count=self._runtime_mismatch_auto_recover_failed_count,
            broker_guard_reject_count=self._broker_guard_reject_count,
            broker_parallelism_guard_skip_count=self._broker_parallelism_guard_skip_count,
            document_symbol_sync_skip_requested_count=self._document_symbol_sync_skip_requested_count,
            document_symbol_sync_skip_accepted_count=self._document_symbol_sync_skip_accepted_count,
            document_symbol_sync_skip_legacy_fallback_count=self._document_symbol_sync_skip_legacy_fallback_count,
            probe_state_unavailable_count=unavailable_count,
            probe_state_workspace_mismatch_count=workspace_mismatch_count,
            probe_state_cooldown_count=cooldown_count,
            probe_state_backpressure_count=backpressure_cooldown_count,
            probe_reconcile_clear_count=self._probe_reconcile_clear_count,
            probe_reconcile_skip_count=self._probe_reconcile_skip_count,
        )

    def mark_repo_hot(self, repo_root: str) -> None:
        marker = getattr(self._hub, "mark_repo_hot", None)
        if callable(marker):
            marker(repo_root)

    def is_repo_hot(self, repo_root: str) -> bool:
        checker = getattr(self._hub, "is_repo_hot", None)
        if callable(checker):
            return bool(checker(repo_root))
        return False

    def _recover_from_runtime_mismatch(self, *, repo_root: str, relative_path: str) -> bool:
        """ERR_RUNTIME_MISMATCH 발생 시 repo/language LSP 런타임을 강제 재시작한다."""
        return self._runtime_mismatch_recovery_service.recover_from_runtime_mismatch(
            hub=self._hub,
            runtime_mismatch_last_restart_at=self._runtime_mismatch_last_restart_at,
            runtime_mismatch_restart_cooldown_sec=self._runtime_mismatch_restart_cooldown_sec,
            repo_root=repo_root,
            relative_path=relative_path,
        )

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
        with self._probe_lock:
            return self._runtime_mismatch_recovery_service.should_force_recover_from_extract_error(
                probe_state=self._probe_state,
                repo_root=repo_root,
                relative_path=relative_path,
                error_code=error_code,
                probe_timeout_window_sec=self._probe_timeout_window_sec,
            )

    def _record_probe_state_from_extract_error(self, *, repo_root: str, relative_path: str, error_code: str, error_message: str) -> None:
        """L3 extract 실패를 probe 상태에 반영해 반복 startup/요청 폭주를 완화한다."""
        with self._probe_lock:
            self._probe_state_update_service.record_extract_error(
                probe_state=self._probe_state,
                repo_root=repo_root,
                relative_path=relative_path,
                error_code=error_code,
                error_message=error_message,
            )
        language = resolve_language_from_path(file_path=relative_path)
        if language is not None:
            self._sync_probe_state_record((repo_root, language))

    def _next_probe_retry_backoff_sec(self, *, error_code: str, fail_count: int) -> float:
        """오류 코드/누적 실패 횟수에 따라 probe 재시도 백오프를 계산한다."""
        return self._probe_state_update_service.next_probe_retry_backoff_sec(
            error_code=error_code,
            fail_count=fail_count,
        )


def _normalize_symbols_subinterp_task(
    repo_root: str,
    normalized_relative_path: str,
    raw_symbols: list[object],
) -> list[dict[str, object]]:
    """subinterpreter에서 실행할 L5 symbol normalize 태스크."""
    symbols: list[dict[str, object]] = []
    for raw in raw_symbols:
        if not isinstance(raw, dict):
            continue
        location = raw.get("location")
        resolved_relative_path = normalized_relative_path
        if isinstance(location, dict):
            resolved_relative_path = normalize_location_to_repo_relative(
                location=location,
                fallback_relative_path=normalized_relative_path,
                repo_root=repo_root,
            )
        if not isinstance(location, dict):
            location = {}
        range_data = location.get("range")
        line = 0
        end_line = 0
        if isinstance(range_data, dict):
            start_data = range_data.get("start")
            end_data = range_data.get("end")
            if isinstance(start_data, dict):
                line = int(start_data.get("line", 0))
            if isinstance(end_data, dict):
                end_line = int(end_data.get("line", line))
        parent_symbol = raw.get("parent")
        parent_symbol_key = _build_symbol_key_subinterp(
            repo_root=repo_root,
            relative_path=resolved_relative_path,
            symbol=parent_symbol,
            fallback_parent_key=None,
        )
        symbol_key = _build_symbol_key_subinterp(
            repo_root=repo_root,
            relative_path=resolved_relative_path,
            symbol=raw,
            fallback_parent_key=parent_symbol_key,
        )
        symbols.append(
            {
                "name": str(raw.get("name", "")),
                "kind": str(raw.get("kind", "")),
                "line": line,
                "end_line": end_line,
                "symbol_key": symbol_key,
                "parent_symbol_key": parent_symbol_key,
                "depth": _resolve_symbol_depth_subinterp(raw),
                "container_name": _resolve_container_name_subinterp(raw),
            }
        )
    return symbols


def _resolve_symbol_depth_subinterp(symbol: dict[str, object]) -> int:
    depth = 0
    current = symbol.get("parent")
    while isinstance(current, dict):
        depth += 1
        current = current.get("parent")
    return depth


def _resolve_container_name_subinterp(symbol: dict[str, object]) -> str | None:
    parent = symbol.get("parent")
    if not isinstance(parent, dict):
        return None
    parent_name = parent.get("name")
    if isinstance(parent_name, str) and parent_name.strip() != "":
        return parent_name
    return None


def _build_symbol_key_subinterp(
    *,
    repo_root: str,
    relative_path: str,
    symbol: object,
    fallback_parent_key: str | None,
) -> str | None:
    if not isinstance(symbol, dict):
        return None
    name = symbol.get("name")
    kind = symbol.get("kind")
    if not isinstance(name, str) or not isinstance(kind, str):
        return None
    line = 0
    end_line = 0
    location = symbol.get("location")
    if isinstance(location, dict):
        range_data = location.get("range")
        if isinstance(range_data, dict):
            start_data = range_data.get("start")
            end_data = range_data.get("end")
            if isinstance(start_data, dict):
                line = int(start_data.get("line", 0))
            if isinstance(end_data, dict):
                end_line = int(end_data.get("line", line))
    parent_key = fallback_parent_key or "root"
    key_text = f"{repo_root}:{relative_path}:{name}:{kind}:{line}:{end_line}:{parent_key}"
    return hashlib.sha1(key_text.encode("utf-8")).hexdigest()
