from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

from sari.core.exceptions import DaemonError, ValidationError
from sari.core.language_registry import resolve_language_from_path
from sari.lsp.document_symbols import request_document_symbols_with_optional_sync
from sari.lsp.hub import LspHub
from sari.lsp.path_normalizer import normalize_location_to_repo_relative, normalize_repo_relative_path
from sari.services.collection.lsp_scope_planner import LspScopePlanner
from sari.services.collection.lsp_runtime_metrics_builder import build_runtime_metrics
from sari.services.collection.lsp_probe_state_update_service import LspProbeStateUpdateService
from sari.services.collection.lsp_broker_guard_service import LspBrokerGuardService
from sari.services.collection.lsp_runtime_mismatch_recovery_service import LspRuntimeMismatchRecoveryService
from sari.services.collection.lsp_scope_runtime_service import LspScopeRuntimeService
from sari.services.collection.lsp_extract_error_mapper import LspExtractErrorMapper
from sari.services.collection.lsp_symbol_normalizer_service import LspSymbolNormalizerService
from sari.services.collection.lsp_session_broker import LspSessionBroker
from sari.services.collection.perf_trace import PerfTracer
from sari.services.collection.watcher_hotness_tracker import WatcherHotnessTracker
from sari.services.collection.solid_lsp_probe_mixin import (
    SolidLspProbeMixin,
    _ProbeStateRecord,
    _extract_error_code_from_message,
    _is_unavailable_probe_error,
    _is_warming_probe_error,
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
            is_batch_broker_throughput_mode_enabled=lambda: bool(self._batch_broker_throughput_mode_enabled),
            get_batch_broker_pending_threshold=lambda: int(self._batch_broker_pending_threshold),
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
        self._lsp_scope_planner_shadow_mode = True
        self._lsp_scope_planner_shadow_count = 0
        self._lsp_scope_planner_applied_count = 0
        self._lsp_scope_planner_fallback_index_building_count = 0
        self._scope_override_hit_count = 0
        self._probe_l1_skipped_batch_count = 0
        self._probe_schedule_skipped_batch_count = 0
        self._runtime_mismatch_auto_recovered_count = 0
        self._runtime_mismatch_auto_recover_failed_count = 0
        self._runtime_mismatch_restart_cooldown_sec = 2.0
        self._runtime_mismatch_last_restart_at: dict[tuple[str, str], float] = {}
        self._session_broker: LspSessionBroker | None = None
        self._watcher_hotness_tracker: WatcherHotnessTracker | None = None
        self._session_broker_enabled = False
        self._batch_broker_throughput_mode_enabled = False
        self._batch_broker_pending_threshold = 4
        self._batch_disable_java_probe = False
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
            is_lsp_scope_planner_shadow_mode=lambda: bool(self._lsp_scope_planner_shadow_mode),
            get_scope_active_languages=lambda: self._scope_active_languages,
            perf_tracer=self._perf_tracer,
            on_scope_override_hit=lambda: setattr(self, "_scope_override_hit_count", int(self._scope_override_hit_count) + 1),
            on_scope_planner_shadow=lambda: setattr(
                self,
                "_lsp_scope_planner_shadow_count",
                int(self._lsp_scope_planner_shadow_count) + 1,
            ),
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
        shadow_mode: bool,
    ) -> None:
        """LSP scope planner를 설정한다. Phase 1 baseline은 shadow_mode 기본."""
        self._lsp_scope_planner = planner
        self._lsp_scope_planner_enabled = bool(enabled) and planner is not None
        self._lsp_scope_planner_shadow_mode = bool(shadow_mode)

    def configure_session_runtime(
        self,
        *,
        session_broker: LspSessionBroker | None,
        watcher_hotness_tracker: WatcherHotnessTracker | None,
        enabled: bool,
        batch_throughput_mode_enabled: bool = False,
        batch_throughput_pending_threshold: int = 4,
        batch_disable_java_probe: bool = False,
    ) -> None:
        """PR3 baseline: broker/hotness를 backend에 주입한다."""
        self._session_broker = session_broker
        self._watcher_hotness_tracker = watcher_hotness_tracker
        self._session_broker_enabled = bool(enabled) and session_broker is not None
        self._batch_broker_throughput_mode_enabled = bool(batch_throughput_mode_enabled)
        self._batch_broker_pending_threshold = max(1, int(batch_throughput_pending_threshold))
        self._batch_disable_java_probe = bool(batch_disable_java_probe)
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
        if lane != "hot":
            return
        broker = self._session_broker
        if not self._session_broker_enabled or broker is None or not broker.is_profiled_language(language):
            return
        plan_fn = getattr(broker, "get_standby_retention_plan", None)
        touch_fn = getattr(self._hub, "touch", None)
        prune_fn = getattr(self._hub, "prune_retention", None)
        if not callable(plan_fn) or not callable(touch_fn):
            return
        try:
            ttl_override_sec, keep_scopes = plan_fn(
                language=language,
                requested_ttl_sec=60.0,
            )
            if runtime_scope_root in keep_scopes and float(ttl_override_sec) > 0.0:
                touch_fn(
                    language=language,
                    repo_root=runtime_scope_root,
                    ttl_override_sec=float(ttl_override_sec),
                    retention_tier="standby",
                    hotness_score=float(hotness_score),
                )
            if callable(prune_fn):
                prune_fn(language=language, keep_repo_roots=set(keep_scopes), retention_tier="standby")
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return

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
            language = self._hub.resolve_language(normalized_relative_path)
            runtime_scope_root, runtime_relative_path = self._resolve_lsp_runtime_scope(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative_path,
                language=language,
            )
            with self._perf_tracer.span("extract_once.ensure_prewarm", phase="l3_extract", repo_root=runtime_scope_root, language=language.value):
                self._ensure_prewarm(language=language, repo_root=runtime_scope_root)
            lsp = self._get_or_start_with_broker_guard(
                language=language,
                runtime_scope_root=runtime_scope_root,
                lane="backlog",
                pending_jobs_in_scope=max(
                    1,
                    self._consume_l3_scope_pending_hint(language=language, runtime_scope_root=runtime_scope_root),
                ),
                request_kind="indexing",
                trace_name="extract_once.get_or_start",
                trace_phase="l3_extract",
            )
            with self._acquire_l1_probe_slot():
                with self._perf_tracer.span("extract_once.document_symbol_request", phase="l3_extract", repo_root=repo_root, language=language.value):
                    self._document_symbol_sync_skip_requested_count += 1
                    document_symbols_result, sync_hint_accepted = request_document_symbols_with_optional_sync(
                        lsp,
                        runtime_relative_path,
                        sync_with_ls=False,
                    )
                    if sync_hint_accepted:
                        self._document_symbol_sync_skip_accepted_count += 1
                    else:
                        self._document_symbol_sync_skip_legacy_fallback_count += 1
                    document_symbols = document_symbols_result.iter_symbols()
                    raw_symbols = list(document_symbols)
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
            symbols = self._symbol_normalizer_service.normalize_symbols(
                repo_root=repo_root,
                normalized_relative_path=normalized_relative_path,
                raw_symbols=raw_symbols,
            )
        return LspExtractionResultDTO(symbols=symbols, relations=[], error_message=None)

    def get_parallelism(self, repo_root: str, language: Language) -> int:
        """현재 언어/레포 풀의 병렬 처리 가능 슬롯 수를 반환한다."""
        if self._is_profiled_broker_language(language):
            self._broker_parallelism_guard_skip_count += 1
            return 1
        running = self._hub.get_running_instance_count(language=language, repo_root=repo_root)
        if running > 0:
            return running
        self._ensure_prewarm(language=language, repo_root=repo_root)
        return max(1, self._hub.get_running_instance_count(language=language, repo_root=repo_root))

    def get_parallelism_for_batch(self, repo_root: str, language: Language, batch_size: int) -> int:
        """배치 크기를 반영해 풀 인스턴스를 확보하고 병렬도를 반환한다."""
        if self._is_profiled_broker_language(language):
            self._broker_parallelism_guard_skip_count += 1
            return 1
        desired = max(1, int(batch_size))
        servers = self._hub.acquire_pool(language=language, repo_root=repo_root, desired=desired, request_kind="indexing")
        return max(1, len(servers))

    def set_bulk_mode(self, repo_root: str, language: Language, enabled: bool) -> None:
        """bulk 인덱싱 모드를 LSP 허브에 전달한다."""
        if self._is_profiled_broker_language(language):
            self._broker_parallelism_guard_skip_count += 1
            return
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

    def get_runtime_metrics(self) -> dict[str, int]:
        """LSP 허브 런타임 메트릭을 반환한다."""
        with self._probe_lock:
            probe_counts = dict(self._probe_trigger_counts)
        return build_runtime_metrics(
            hub_metrics=dict(self._hub.get_metrics()),
            probe_trigger_counts=probe_counts,
            scope_planner_shadow_count=self._lsp_scope_planner_shadow_count,
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
            probe_l1_skipped_batch_count=self._probe_l1_skipped_batch_count,
            probe_schedule_skipped_batch_count=self._probe_schedule_skipped_batch_count,
        )

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

    def _next_probe_retry_backoff_sec(self, *, error_code: str, fail_count: int) -> float:
        """오류 코드/누적 실패 횟수에 따라 probe 재시도 백오프를 계산한다."""
        return self._probe_state_update_service.next_probe_retry_backoff_sec(
            error_code=error_code,
            fail_count=fail_count,
        )
