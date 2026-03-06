from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from solidlsp.ls_config import Language
from solidlsp.ls_exceptions import SolidLSPException

from sari.core.exceptions import DaemonError
from sari.core.language.registry import resolve_language_from_path
from sari.core.models import now_iso8601_utc
from sari.lsp.document_symbols import request_document_symbols_with_optional_sync
from sari.lsp.path_normalizer import normalize_repo_relative_path

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

class SolidLspProbeMixin:
    def _is_manual_probe_trigger(self, trigger: str | None) -> bool:
        normalized = trigger.strip().lower() if isinstance(trigger, str) else ""
        return normalized in {"manual", "force", "manual_probe", "manual_index", "interactive"}

    def _request_kind_for_probe_trigger(self, trigger: str | None) -> str:
        return "manual_probe" if self._is_manual_probe_trigger(trigger) else "background_probe"

    def _probe_status_for_error_code(self, error_code: str) -> str:
        if error_code == "ERR_LSP_WORKSPACE_MISMATCH":
            return "WORKSPACE_MISMATCH"
        if error_code in {"ERR_LSP_GLOBAL_SOFT_LIMIT", "ERR_LSP_SLOT_EXHAUSTED"}:
            return "BACKPRESSURE_COOLDOWN"
        if _is_unavailable_probe_error(error_code):
            return "UNAVAILABLE_COOLDOWN"
        return "COOLDOWN"

    def _sync_probe_state_record(self, key: tuple[str, Language]) -> None:
        repo = getattr(self, "_repo_language_probe_repo", None)
        if repo is None:
            return
        with self._probe_lock:
            state = self._probe_state.get(key)
            phase = self._probe_inflight_phase.get(key)
        if state is None:
            repo.clear_states(repo_root=key[0], language=key[1].value)
            return
        now_iso = now_iso8601_utc()
        repo.upsert_state(
            repo_root=key[0],
            language=key[1].value,
            status=state.status,
            fail_count=state.fail_count,
            inflight_phase=phase,
            next_retry_at=_next_retry_at_iso(
                next_retry_monotonic=state.next_retry_monotonic,
                last_seen_monotonic=state.last_seen_monotonic,
            ),
            last_error_code=state.last_error_code,
            last_error_message=state.last_error_message,
            last_trigger=state.last_trigger,
            last_seen_at=_monotonic_to_iso(state.last_seen_monotonic),
            updated_at=now_iso,
        )

    def _clear_persisted_probe_state(self, key: tuple[str, Language]) -> None:
        repo = getattr(self, "_repo_language_probe_repo", None)
        if repo is None:
            return
        repo.clear_states(repo_root=key[0], language=key[1].value)

    def _mark_probe_ready(self, key: tuple[str, Language], *, now_mono: float, clear_errors: bool = True) -> None:
        with self._probe_lock:
            state = self._probe_state.get(key)
            if state is None:
                state = _ProbeStateRecord(status="IDLE", last_seen_monotonic=now_mono)
                self._probe_state[key] = state
            if state.status in {"UNAVAILABLE_COOLDOWN", "COOLDOWN", "WORKSPACE_MISMATCH", "BACKPRESSURE_COOLDOWN"}:
                self._probe_reconcile_clear_count = int(getattr(self, "_probe_reconcile_clear_count", 0)) + 1
            else:
                self._probe_reconcile_skip_count = int(getattr(self, "_probe_reconcile_skip_count", 0)) + 1
            state.status = "READY_L0"
            state.fail_count = 0
            state.warming_count = 0
            state.next_retry_monotonic = 0.0
            state.last_seen_monotonic = now_mono
            if clear_errors:
                state.last_error_code = None
                state.last_error_message = None
                state.last_error_time_monotonic = None
        self._sync_probe_state_record(key)

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
        result = "scheduled"
        should_sync = False
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
            if state.status == "READY_L0":
                state.last_trigger = normalized_trigger
                should_sync = True
                result = "ready"
            elif state.status == "WORKSPACE_MISMATCH":
                if force:
                    state.status = "IDLE"
                    state.next_retry_monotonic = 0.0
                    state.last_error_code = None
                    state.last_error_message = None
                    state.last_trigger = normalized_trigger
                else:
                    return "workspace_mismatch"
            if result == "ready":
                pass
            elif state.status == "WARMING":
                if (not force) and now < state.next_retry_monotonic:
                    if self._is_manual_probe_trigger(normalized_trigger):
                        state.last_trigger = normalized_trigger
                    should_sync = True
                    result = "warming"
            bypass_backpressure = self._is_manual_probe_trigger(normalized_trigger) and state.status == "BACKPRESSURE_COOLDOWN"
            if result == "scheduled" and (not force) and now < state.next_retry_monotonic and not bypass_backpressure:
                should_sync = True
                result = "cooldown"
            if result == "scheduled":
                state.last_trigger = normalized_trigger
                future = self._probe_executor.submit(self._probe_worker, key, normalized_relative_path)
                self._probe_inflight[key] = future
                self._probe_inflight_phase[key] = "probe"
                self._probe_trigger_counts[normalized_trigger] = int(self._probe_trigger_counts.get(normalized_trigger, 0)) + 1
                should_sync = True
        if should_sync:
            self._sync_probe_state_record(key)
        return result

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
            state.last_error_time_monotonic = None
        self._sync_probe_state_record(key)

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
        repo = getattr(self, "_repo_language_probe_repo", None)
        if repo is not None:
            repo.clear_states()

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
            if state.status == "BACKPRESSURE_COOLDOWN":
                return now < state.next_retry_monotonic
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
                if state.status not in {"COOLDOWN", "UNAVAILABLE_COOLDOWN", "WORKSPACE_MISMATCH", "BACKPRESSURE_COOLDOWN"}:
                    continue
                state.status = "IDLE"
                state.fail_count = 0
                state.warming_count = 0
                state.next_retry_monotonic = 0.0
                state.last_error_code = None
                state.last_error_message = None
                state.last_error_time_monotonic = None
                cleared += 1
                self._clear_persisted_probe_state((key_root, key_lang))
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
            state.last_seen_monotonic = now
        self._sync_probe_state_record(key)
        try:
            repo_root, language = key
            resolver = getattr(self, "_resolve_probe_runtime_scope", None)
            if callable(resolver):
                runtime_scope_root, runtime_relative_path = resolver(
                    repo_root=repo_root,
                    sample_relative_path=sample_relative_path,
                    language=language,
                )
            else:
                runtime_scope_root, runtime_relative_path = (repo_root, sample_relative_path)
            self._ensure_prewarm(language=language, repo_root=runtime_scope_root)
            with self._probe_lock:
                state = self._probe_state.get(key)
                last_trigger = state.last_trigger if state is not None else None
            request_kind = self._request_kind_for_probe_trigger(last_trigger)
            guarded_get_or_start = getattr(self, "_get_or_start_with_broker_guard", None)
            if callable(guarded_get_or_start):
                lsp = guarded_get_or_start(
                    language=language,
                    runtime_scope_root=runtime_scope_root,
                    lane="backlog",
                    pending_jobs_in_scope=0,
                    request_kind=request_kind,
                    trace_name="probe.get_or_start",
                    trace_phase="probe",
                )
            else:
                lsp = self._hub.get_or_start(language=language, repo_root=runtime_scope_root, request_kind=request_kind)
            self._mark_probe_ready(key, now_mono=now)
            status = "success"
            if language in {Language.GO, Language.JAVA, Language.KOTLIN}:
                l1_future = self._l1_executor.submit(self._run_l1_probe_tracked, key, runtime_relative_path)
                with self._probe_lock:
                    self._probe_inflight[key] = l1_future
                    self._probe_inflight_phase[key] = "l1"
                self._sync_probe_state_record(key)
                handed_off_to_l1 = True
        except (SolidLSPException, DaemonError, RuntimeError, OSError, ValueError, TypeError) as exc:
            error_message = str(exc)
            error_code = _extract_error_code_from_message(error_message)
            with self._probe_lock:
                state = self._probe_state[key]
                state.status = self._probe_status_for_error_code(error_code)
                state.fail_count += 1
                state.last_error_code = error_code
                state.last_error_message = error_message
                state.last_error_time_monotonic = now
                state.next_retry_monotonic = now + self._next_probe_retry_backoff_sec(
                    error_code=error_code,
                    fail_count=state.fail_count,
                )
            self._sync_probe_state_record(key)
            status = "failure"
        finally:
            with self._probe_lock:
                state = self._probe_state.get(key)
                if state is not None:
                    state.last_seen_monotonic = time.monotonic()
                if not handed_off_to_l1:
                    self._probe_inflight.pop(key, None)
                    self._probe_inflight_phase.pop(key, None)
            self._sync_probe_state_record(key)
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
            self._sync_probe_state_record(key)

    def _run_l1_probe(self, key: tuple[str, Language], sample_relative_path: str) -> None:
        """READY_L0 이후 L1(documentSymbol) probe를 지연 실행한다."""
        now = time.monotonic()
        try:
            repo_root, language = key
            with self._probe_lock:
                state = self._probe_state.get(key)
                request_kind = self._request_kind_for_probe_trigger(state.last_trigger if state is not None else None)
            resolver = getattr(self, "_resolve_probe_runtime_scope", None)
            if callable(resolver):
                runtime_scope_root, runtime_relative_path = resolver(
                    repo_root=repo_root,
                    sample_relative_path=sample_relative_path,
                    language=language,
                )
            else:
                runtime_scope_root, runtime_relative_path = (repo_root, sample_relative_path)
            guarded_get_or_start = getattr(self, "_get_or_start_with_broker_guard", None)
            if callable(guarded_get_or_start):
                lsp = guarded_get_or_start(
                    language=language,
                    runtime_scope_root=runtime_scope_root,
                    lane="backlog",
                    pending_jobs_in_scope=0,
                    request_kind=request_kind,
                    trace_name="probe_l1.get_or_start",
                    trace_phase="probe",
                )
            else:
                lsp = self._hub.get_or_start(language=language, repo_root=runtime_scope_root, request_kind=request_kind)
            with self._acquire_l1_probe_slot():
                symbols_result, _sync_hint_accepted = request_document_symbols_with_optional_sync(
                    lsp,
                    runtime_relative_path,
                    sync_with_ls=False,
                )
                _ = list(symbols_result.iter_symbols())
            self._mark_probe_ready(key, now_mono=now)
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
                    state.status = self._probe_status_for_error_code(error_code)
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
            self._sync_probe_state_record(key)


def _monotonic_to_iso(value: float | None) -> str | None:
    if value is None or value <= 0.0:
        return None
    return now_iso8601_utc()


def _next_retry_at_iso(*, next_retry_monotonic: float, last_seen_monotonic: float) -> str | None:
    if next_retry_monotonic <= 0.0:
        return None
    if next_retry_monotonic == float("inf"):
        return None
    if last_seen_monotonic <= 0.0:
        return None
    delta_sec = max(0.0, next_retry_monotonic - last_seen_monotonic)
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_sec)).isoformat()

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
    if "soft limit" in lowered and "lsp" in lowered:
        return "ERR_LSP_GLOBAL_SOFT_LIMIT"
    if "슬롯" in lowered and "lsp" in lowered:
        return "ERR_LSP_SLOT_EXHAUSTED"
    if "slot" in lowered and "lsp" in lowered:
        return "ERR_LSP_SLOT_EXHAUSTED"
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
