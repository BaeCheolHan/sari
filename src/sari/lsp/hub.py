"""Serena solidlsp 기반 LSP Hub를 제공한다."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
import threading
import time
from typing import Callable

from sari.core.language_registry import resolve_language_from_path
from sari.core.exceptions import DaemonError, ErrorContext
from sari.lsp.runtime_broker import LspRuntimeBroker
from sari.services.collection.perf_trace import PerfTracer
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.settings import SolidLSPSettings

log = logging.getLogger(__name__)
try:
    import certifi
except (ImportError, RuntimeError, OSError):
    certifi = None


@dataclass(frozen=True)
class LspRuntimeKey:
    """LSP 인스턴스 식별 키를 정의한다."""

    language: Language
    repo_root: str
    slot: int


@dataclass
class LspRuntimeEntry:
    """LSP 인스턴스와 마지막 사용 시각을 함께 보관한다."""

    server: SolidLanguageServer
    last_used_at: float
    retention_expires_at: float = 0.0
    retention_tier: str | None = None
    retention_hotness: float = 0.0


class LspHub:
    """언어별 LSP 인스턴스 생명주기를 관리한다."""

    def __init__(
        self,
        idle_timeout_sec: int = 900,
        max_instances: int = 32,
        max_instances_per_repo_language: int = 1,
        bulk_mode_enabled: bool = True,
        bulk_max_instances_per_repo_language: int = 4,
        lsp_global_soft_limit: int = 0,
        hot_acquire_window_sec: float = 1.0,
        scale_out_hot_hits: int = 24,
        idle_cleanup_interval_sec: float = 5.0,
        stop_timeout_sec: float = 3.0,
        request_timeout_sec: float = 20.0,
        file_buffer_idle_ttl_sec: float = 20.0,
        file_buffer_max_open: int = 512,
        interactive_reserved_slots_per_repo_language: int = 0,
        interactive_timeout_sec: float = 2.5,
        java_min_major: int = 17,
        max_concurrent_starts: int = 2,
        max_concurrent_l1_probes: int = 2,
        runtime_broker: LspRuntimeBroker | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """내부 인스턴스 캐시를 초기화한다."""
        self._instances: dict[LspRuntimeKey, LspRuntimeEntry] = {}
        self._idle_timeout_sec = max(1, idle_timeout_sec)
        self._max_instances = max(1, max_instances)
        self._max_instances_per_repo_language = max(1, max_instances_per_repo_language)
        self._bulk_mode_enabled = bool(bulk_mode_enabled)
        self._bulk_max_instances_per_repo_language = max(
            self._max_instances_per_repo_language,
            int(bulk_max_instances_per_repo_language),
        )
        self._bulk_active_keys: set[tuple[Language, str]] = set()
        self._lsp_global_soft_limit = max(0, lsp_global_soft_limit)
        self._hot_acquire_window_sec = max(0.1, hot_acquire_window_sec)
        self._scale_out_hot_hits = max(2, scale_out_hot_hits)
        self._round_robin_cursor: dict[tuple[Language, str], int] = {}
        self._last_acquire_at: dict[tuple[Language, str], float] = {}
        self._hot_acquire_hits: dict[tuple[Language, str], int] = {}
        self._starting_events: dict[LspRuntimeKey, threading.Event] = {}
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._idle_cleanup_interval_sec = max(0.5, idle_cleanup_interval_sec)
        self._stop_timeout_sec = max(0.2, stop_timeout_sec)
        self._request_timeout_sec = max(0.1, request_timeout_sec)
        self._file_buffer_idle_ttl_sec = max(1.0, float(file_buffer_idle_ttl_sec))
        self._file_buffer_max_open = max(16, int(file_buffer_max_open))
        self._interactive_reserved_slots_per_repo_language = max(0, int(interactive_reserved_slots_per_repo_language))
        self._interactive_timeout_sec = max(0.1, float(interactive_timeout_sec))
        self._interactive_pending_count = 0
        self._interactive_timeout_count = 0
        self._interactive_rejected_count = 0
        self._runtime_broker = runtime_broker if runtime_broker is not None else LspRuntimeBroker(java_min_major=java_min_major)
        self._environment_patch_lock = threading.RLock()
        self._start_semaphore = threading.Semaphore(max(1, int(max_concurrent_starts)))
        self._l1_probe_semaphore = threading.Semaphore(max(1, int(max_concurrent_l1_probes)))
        self._start_semaphore_wait_ms_total = 0.0
        self._l1_probe_semaphore_wait_ms_total = 0.0
        self._scale_out_guard_block_count = 0
        self._retention_touch_count = 0
        self._retention_prune_count = 0
        self._scale_out_guard: Callable[[Language, str], bool] | None = None
        self._stop_cleanup = threading.Event()
        self._forced_kill_count = 0
        self._stop_timeout_count = 0
        self._orphan_suspect_count = 0
        self._cleanup_thread = threading.Thread(
            target=self._idle_cleanup_loop,
            name="sari-lsp-idle-cleaner",
            daemon=True,
        )
        self._cleanup_thread.start()
        self._perf_tracer = PerfTracer(component="lsp_hub")

    def resolve_language(self, file_path: str) -> Language:
        """파일 확장자로 언어를 결정한다."""
        resolved = resolve_language_from_path(file_path=file_path)
        if resolved is not None:
            return resolved
        raise DaemonError(ErrorContext(code="ERR_UNSUPPORTED_LANGUAGE", message="지원하지 않는 언어 확장자입니다"))

    def get_or_start(self, language: Language, repo_root: str, request_kind: str = "indexing") -> SolidLanguageServer:
        """언어/저장소 기준으로 LSP를 가져오거나 시작한다."""
        normalized_root = str(Path(repo_root).resolve())
        base_key = (language, normalized_root)
        normalized_kind = self._normalize_request_kind(request_kind)
        wait_timeout_sec = self._interactive_timeout_sec if normalized_kind == "interactive" else self._request_timeout_sec
        with self._perf_tracer.span(
            "get_or_start.total",
            phase="lsp_hub",
            language=language.value,
            repo_root=normalized_root,
            request_kind=normalized_kind,
        ):
            if normalized_kind == "interactive":
                with self._lock:
                    self._interactive_pending_count += 1
            try:
                with self._perf_tracer.span(
                    "get_or_start.lock_select",
                    phase="lsp_hub",
                    language=language.value,
                    repo_root=normalized_root,
                    request_kind=normalized_kind,
                ):
                    with self._lock:
                        now = self._clock()
                        self._evict_idle_locked(now)
                        existing_keys = self._runtime_keys_for_locked(language=language, repo_root=normalized_root)
                        running_keys: list[LspRuntimeKey] = []
                        for key in existing_keys:
                            entry = self._instances.get(key)
                            if entry is None:
                                continue
                            if entry.server.server.is_running():
                                if not self._is_slot_allowed_for_kind(
                                    base_key=base_key,
                                    slot=key.slot,
                                    request_kind=normalized_kind,
                                ):
                                    continue
                                running_keys.append(key)
                                continue
                            self._cleanup_not_running_entry_locked(key=key, entry=entry)

                        should_scale_out = self._should_scale_out_locked(base_key=base_key, now=now, running_count=len(running_keys))
                        if should_scale_out:
                            try:
                                slot = self._next_slot_locked(
                                    language=language,
                                    repo_root=normalized_root,
                                    request_kind=normalized_kind,
                                )
                            except DaemonError as exc:
                                if exc.context.code != "ERR_LSP_SLOT_EXHAUSTED" or len(running_keys) == 0:
                                    if normalized_kind == "interactive":
                                        self._interactive_rejected_count += 1
                                    raise
                                selected_key = self._select_round_robin_key_locked(base_key=base_key, keys=running_keys)
                                selected_entry = self._instances[selected_key]
                                selected_entry.last_used_at = now
                                self._last_acquire_at[base_key] = now
                                return selected_entry.server
                            self._last_acquire_at[base_key] = now
                        elif len(running_keys) > 0:
                            selected_key = self._select_round_robin_key_locked(base_key=base_key, keys=running_keys)
                            selected_entry = self._instances[selected_key]
                            selected_entry.last_used_at = now
                            self._last_acquire_at[base_key] = now
                            return selected_entry.server
                        else:
                            self._last_acquire_at[base_key] = now
                            slot = self._first_allowed_slot(base_key=base_key, request_kind=normalized_kind)

                return self._start_or_wait_for_slot(
                    language=language,
                    repo_root=normalized_root,
                    slot=slot,
                    wait_timeout_sec=wait_timeout_sec,
                    request_kind=normalized_kind,
                )
            finally:
                if normalized_kind == "interactive":
                    with self._lock:
                        self._interactive_pending_count = max(0, self._interactive_pending_count - 1)

    def ensure_healthy(self, language: Language, repo_root: str) -> None:
        """등록된 LSP 인스턴스의 실행 상태를 확인한다."""
        normalized_root = str(Path(repo_root).resolve())
        keys = self._runtime_keys_for(language=language, repo_root=normalized_root)
        if len(keys) == 0:
            return
        with self._lock:
            for key in keys:
                entry = self._instances.get(key)
                if entry is not None and entry.server.server.is_running():
                    return
        raise DaemonError(ErrorContext(code="ERR_LSP_UNHEALTHY", message="LSP 서버가 비정상 상태입니다"))

    def restart_if_unhealthy(self, language: Language, repo_root: str) -> SolidLanguageServer:
        """비정상 LSP 인스턴스를 정리한 뒤 재시작한다."""
        normalized_root = str(Path(repo_root).resolve())
        keys = self._runtime_keys_for(language=language, repo_root=normalized_root)
        with self._lock:
            for key in keys:
                entry = self._instances.get(key)
                if entry is None:
                    continue
                try:
                    self._stop_server_with_timeout(entry.server)
                except (RuntimeError, OSError, ValueError) as exc:
                    log.warning("비정상 LSP 종료 실패(language=%s, repo=%s): %s", key.language.value, key.repo_root, exc)
                except DaemonError as exc:
                    log.warning("비정상 LSP 종료 타임아웃(language=%s, repo=%s): %s", key.language.value, key.repo_root, exc.context.code)
                self._instances.pop(key, None)
        return self.get_or_start(language=language, repo_root=normalized_root)

    def prewarm_language_pool(self, language: Language, repo_root: str) -> None:
        """지정 언어/저장소의 LSP 풀을 목표 슬롯 수까지 선기동한다."""
        normalized_root = str(Path(repo_root).resolve())
        if self._max_instances_per_repo_language <= 1:
            return
        while True:
            with self._lock:
                now = self._clock()
                self._evict_idle_locked(now)
                running_keys: list[LspRuntimeKey] = []
                for key in self._runtime_keys_for_locked(language=language, repo_root=normalized_root):
                    entry = self._instances.get(key)
                    if entry is None:
                        continue
                    if entry.server.server.is_running():
                        running_keys.append(key)
                        continue
                    self._cleanup_not_running_entry_locked(key=key, entry=entry)
                if len(running_keys) >= self._max_instances_per_repo_language:
                    return
                try:
                    slot = self._next_slot_locked(language=language, repo_root=normalized_root, request_kind="indexing")
                except DaemonError as exc:
                    if exc.context.code == "ERR_LSP_SLOT_EXHAUSTED":
                        return
                    raise
            self._start_or_wait_for_slot(
                language=language,
                repo_root=normalized_root,
                slot=slot,
                wait_timeout_sec=self._request_timeout_sec,
                request_kind="indexing",
            )

    def stop_all(self) -> None:
        """Hub가 관리하는 LSP 서버를 모두 종료한다."""
        self._stop_cleanup.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=max(1.0, self._idle_cleanup_interval_sec * 2.0))
        failure_messages: list[str] = []
        with self._lock:
            for key, entry in list(self._instances.items()):
                try:
                    self._stop_server_with_timeout(entry.server)
                except (RuntimeError, OSError, ValueError) as exc:
                    # 종료 실패를 누락하지 않도록 로그를 남긴다.
                    log.exception("LSP 서버 종료 실패(language=%s, repo=%s): %s", key.language.value, key.repo_root, exc)
                    failure_messages.append(f"{key.language.value}@{key.repo_root}: {exc}")
                except DaemonError as exc:
                    log.exception("LSP 서버 종료 타임아웃(language=%s, repo=%s): %s", key.language.value, key.repo_root, exc.context.message)
                    failure_messages.append(f"{key.language.value}@{key.repo_root}: {exc.context.code}")
                self._instances.pop(key, None)
            for event in self._starting_events.values():
                event.set()
            self._starting_events.clear()
        if len(failure_messages) > 0:
            message = f"LSP 서버 종료 실패 {len(failure_messages)}건: " + "; ".join(failure_messages[:3])
            raise DaemonError(ErrorContext(code="ERR_LSP_STOP_FAILED", message=message))

    def get_metrics(self) -> dict[str, int]:
        """LSP 런타임 운영 메트릭 스냅샷을 반환한다."""
        with self._lock:
            return {
                "lsp_instance_count": len(self._instances),
                "lsp_forced_kill_count": int(self._forced_kill_count),
                "lsp_stop_timeout_count": int(self._stop_timeout_count),
                "lsp_orphan_suspect_count": int(self._orphan_suspect_count),
                "lsp_interactive_pending_count": int(self._interactive_pending_count),
                "lsp_interactive_timeout_count": int(self._interactive_timeout_count),
                "lsp_interactive_rejected_count": int(self._interactive_rejected_count),
                "lsp_start_semaphore_wait_ms_total": int(self._start_semaphore_wait_ms_total),
                "lsp_l1_probe_semaphore_wait_ms_total": int(self._l1_probe_semaphore_wait_ms_total),
                "lsp_scale_out_guard_block_count": int(self._scale_out_guard_block_count),
                "lsp_retention_touch_count": int(self._retention_touch_count),
                "lsp_retention_prune_count": int(self._retention_prune_count),
            }

    def set_scale_out_guard(self, guard: Callable[[Language, str], bool] | None) -> None:
        """추가 scale-out 증설 차단 가드를 설정한다 (첫 기동은 허용)."""
        with self._lock:
            self._scale_out_guard = guard

    def touch(
        self,
        *,
        language: Language,
        repo_root: str,
        ttl_override_sec: float,
        retention_tier: str = "standby",
        hotness_score: float = 0.0,
    ) -> int:
        """해당 언어/스코프 인스턴스의 idle eviction 보호 만료시각을 연장한다."""
        normalized_root = str(Path(repo_root).resolve())
        ttl_sec = max(0.0, float(ttl_override_sec))
        if ttl_sec <= 0.0:
            return 0
        now = self._clock()
        changed = 0
        with self._lock:
            for key in self._runtime_keys_for_locked(language=language, repo_root=normalized_root):
                entry = self._instances.get(key)
                if entry is None or not entry.server.server.is_running():
                    continue
                new_exp = now + ttl_sec
                entry.last_used_at = now
                entry.retention_expires_at = max(float(entry.retention_expires_at), float(new_exp))
                entry.retention_tier = retention_tier
                entry.retention_hotness = max(float(entry.retention_hotness), float(hotness_score))
                changed += 1
            if changed > 0:
                self._retention_touch_count += changed
        return changed

    def prune_retention(
        self,
        *,
        language: Language,
        keep_repo_roots: set[str],
        retention_tier: str = "standby",
    ) -> int:
        """지정 언어의 retention 보호를 keep set 외 범위에서 해제한다."""
        normalized_keep = {str(Path(root).resolve()) for root in keep_repo_roots}
        changed = 0
        with self._lock:
            for key, entry in self._instances.items():
                if key.language != language:
                    continue
                if retention_tier and entry.retention_tier != retention_tier:
                    continue
                if key.repo_root in normalized_keep:
                    continue
                if entry.retention_expires_at > 0.0 or entry.retention_tier is not None:
                    entry.retention_expires_at = 0.0
                    entry.retention_tier = None
                    entry.retention_hotness = 0.0
                    changed += 1
            if changed > 0:
                self._retention_prune_count += changed
        return changed

    @contextmanager
    def acquire_l1_probe_slot(self):
        """documentSymbol 등 L1 probe 요청 동시성을 제한한다."""
        started_at = self._clock()
        self._l1_probe_semaphore.acquire()
        waited_ms = max(0.0, float(self._clock() - started_at) * 1000.0)
        with self._lock:
            self._l1_probe_semaphore_wait_ms_total += waited_ms
        try:
            yield
        finally:
            self._l1_probe_semaphore.release()

    def get_interactive_pressure(self) -> dict[str, int]:
        """인터랙티브 요청 압력 지표를 반환한다."""
        with self._lock:
            return {
                "pending_interactive": int(self._interactive_pending_count),
                "interactive_timeout_count": int(self._interactive_timeout_count),
                "interactive_rejected_count": int(self._interactive_rejected_count),
            }

    def get_running_instance_count(self, language: Language, repo_root: str) -> int:
        """지정 언어/저장소 조합의 실행 중 인스턴스 수를 반환한다."""
        normalized_root = str(Path(repo_root).resolve())
        with self._lock:
            running = 0
            for key in self._runtime_keys_for_locked(language=language, repo_root=normalized_root):
                entry = self._instances.get(key)
                if entry is None:
                    continue
                if entry.server.server.is_running():
                    running += 1
            return running

    def acquire_pool(self, language: Language, repo_root: str, desired: int, request_kind: str = "indexing") -> list[SolidLanguageServer]:
        """요청된 개수만큼 풀 인스턴스를 확보해 반환한다."""
        normalized_root = str(Path(repo_root).resolve())
        normalized_kind = self._normalize_request_kind(request_kind)
        target_count = max(1, min(self._max_instances_for_key((language, normalized_root)), int(desired)))
        first = self.get_or_start(language=language, repo_root=normalized_root, request_kind=normalized_kind)
        servers: list[SolidLanguageServer] = [first]
        self.prewarm_language_pool(language=language, repo_root=normalized_root)
        with self._lock:
            running_keys: list[LspRuntimeKey] = []
            for key in self._runtime_keys_for_locked(language=language, repo_root=normalized_root):
                entry = self._instances.get(key)
                if entry is not None and entry.server.server.is_running():
                    if not self._is_slot_allowed_for_kind(
                        base_key=(language, normalized_root),
                        slot=key.slot,
                        request_kind=normalized_kind,
                    ):
                        continue
                    running_keys.append(key)
            for key in running_keys:
                entry = self._instances.get(key)
                if entry is None:
                    continue
                if entry.server not in servers:
                    servers.append(entry.server)
                if len(servers) >= target_count:
                    break
        return servers[:target_count]

    def reconcile_runtime(self) -> int:
        """비정상/유휴 LSP 엔트리를 즉시 정리하고 정리 건수를 반환한다."""
        with self._lock:
            before_count = len(self._instances)
            now = self._clock()
            for key in list(self._instances.keys()):
                entry = self._instances.get(key)
                if entry is None:
                    continue
                if entry.server.server.is_running():
                    continue
                self._cleanup_not_running_entry_locked(key=key, entry=entry)
            self._evict_idle_locked(now)
            return max(0, before_count - len(self._instances))

    def set_bulk_mode(self, language: Language, repo_root: str, enabled: bool) -> None:
        """언어/저장소 키의 bulk 모드 활성 상태를 설정한다."""
        if not self._bulk_mode_enabled:
            return
        key = (language, str(Path(repo_root).resolve()))
        with self._lock:
            if enabled:
                self._bulk_active_keys.add(key)
            else:
                self._bulk_active_keys.discard(key)

    def _evict_idle_locked(self, now: float) -> None:
        """idle timeout을 초과한 인스턴스를 정리한다."""
        evict_keys = [
            key
            for key, entry in self._instances.items()
            if (now - entry.last_used_at) >= float(self._idle_timeout_sec)
            and now >= float(entry.retention_expires_at)
        ]
        for key in evict_keys:
            self._stop_entry_locked(key)

    def _evict_lru_if_needed_locked(self) -> None:
        """최대 인스턴스 수를 넘기기 전에 LRU 인스턴스를 정리한다."""
        while len(self._instances) >= self._max_instances:
            def _lru_key_sort(key: LspRuntimeKey) -> tuple[int, float, float]:
                entry = self._instances[key]
                # retention 보호가 없는 항목을 먼저 정리한다.
                retention_rank = 1 if entry.retention_expires_at > self._clock() else 0
                return (retention_rank, float(entry.last_used_at), float(entry.retention_hotness))
            lru_key = min(self._instances.keys(), key=_lru_key_sort)
            self._stop_entry_locked(lru_key)

    def _stop_entry_locked(self, key: LspRuntimeKey) -> None:
        """단일 인스턴스를 종료하고 캐시에서 제거한다."""
        entry = self._instances.get(key)
        if entry is None:
            return
        try:
            self._stop_server_with_timeout(entry.server)
        except (RuntimeError, OSError, ValueError) as exc:
            log.exception("LSP 인스턴스 정리 실패(language=%s, repo=%s): %s", key.language.value, key.repo_root, exc)
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_EVICT_FAILED",
                    message=f"LSP 인스턴스 정리에 실패했습니다: {key.language.value}@{key.repo_root}",
                )
            ) from exc
        self._instances.pop(key, None)
        base_key = (key.language, key.repo_root)
        self._round_robin_cursor.pop(base_key, None)
        self._hot_acquire_hits.pop(base_key, None)

    def _cleanup_not_running_entry_locked(self, key: LspRuntimeKey, entry: LspRuntimeEntry) -> None:
        """is_running=false 엔트리를 OS 프로세스까지 정리한다."""
        self._orphan_suspect_count += 1
        try:
            self._stop_server_with_timeout(entry.server)
        except (RuntimeError, OSError, ValueError) as exc:
            log.warning(
                "비정상 LSP 엔트리 정리 실패(language=%s, repo=%s): %s",
                key.language.value,
                key.repo_root,
                exc,
            )
        except DaemonError as exc:
            log.warning(
                "비정상 LSP 엔트리 stop 타임아웃(language=%s, repo=%s): %s",
                key.language.value,
                key.repo_root,
                exc.context.code,
            )
        self._instances.pop(key, None)
        base_key = (key.language, key.repo_root)
        self._round_robin_cursor.pop(base_key, None)
        self._hot_acquire_hits.pop(base_key, None)

    def _runtime_keys_for(self, language: Language, repo_root: str) -> list[LspRuntimeKey]:
        """언어/저장소 조합에 해당하는 런타임 키 목록을 조회한다."""
        with self._lock:
            return self._runtime_keys_for_locked(language=language, repo_root=repo_root)

    def _runtime_keys_for_locked(self, language: Language, repo_root: str) -> list[LspRuntimeKey]:
        """락이 잡힌 상태에서 언어/저장소 조합의 키 목록을 조회한다."""
        return sorted(
            [key for key in self._instances.keys() if key.language == language and key.repo_root == repo_root],
            key=lambda key: key.slot,
        )

    def _next_slot_locked(self, language: Language, repo_root: str, request_kind: str = "indexing") -> int:
        """새로운 인스턴스에 사용할 슬롯 번호를 계산한다."""
        used_slots = {key.slot for key in self._runtime_keys_for_locked(language=language, repo_root=repo_root)}
        max_slots = self._max_instances_for_key((language, repo_root))
        for slot in range(max_slots):
            if not self._is_slot_allowed_for_kind(
                base_key=(language, repo_root),
                slot=slot,
                request_kind=request_kind,
            ):
                continue
            if slot not in used_slots:
                return slot
        raise DaemonError(
            ErrorContext(
                code="ERR_LSP_SLOT_EXHAUSTED",
                message=f"LSP 슬롯이 모두 사용 중입니다: {language.value}@{repo_root}",
            )
        )

    def _should_scale_out_locked(self, base_key: tuple[Language, str], now: float, running_count: int) -> bool:
        """짧은 시간 내 재요청이 몰리면 동일 언어/레포 풀을 확장한다."""
        self._record_hot_hit_locked(base_key=base_key, now=now)
        if running_count == 0:
            return True
        guard = self._scale_out_guard
        if guard is not None:
            try:
                if bool(guard(base_key[0], base_key[1])):
                    self._scale_out_guard_block_count += 1
                    return False
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                # guard 오류는 서비스 가용성보다 낮은 우선순위다.
                ...
        # 전역 소프트 상한을 넘기면 추가 scale-out을 차단한다.
        if self._lsp_global_soft_limit > 0 and len(self._instances) >= self._lsp_global_soft_limit:
            return False
        max_instances_for_key = self._max_instances_for_key(base_key)
        if running_count >= max_instances_for_key:
            return False
        hits = self._hot_acquire_hits.get(base_key, 0)
        if base_key in self._bulk_active_keys:
            return hits >= max(2, self._scale_out_hot_hits // 2)
        return hits >= self._scale_out_hot_hits

    def _max_instances_for_key(self, base_key: tuple[Language, str]) -> int:
        """키별 허용 인스턴스 상한을 계산한다."""
        if self._bulk_mode_enabled and base_key in self._bulk_active_keys:
            return max(self._max_instances_per_repo_language, self._bulk_max_instances_per_repo_language)
        return self._max_instances_per_repo_language

    def _record_hot_hit_locked(self, base_key: tuple[Language, str], now: float) -> None:
        """동일 키의 단기 호출 누적 횟수를 갱신한다."""
        last = self._last_acquire_at.get(base_key)
        if last is None or (now - last) > self._hot_acquire_window_sec:
            self._hot_acquire_hits[base_key] = 1
            return
        self._hot_acquire_hits[base_key] = self._hot_acquire_hits.get(base_key, 0) + 1

    def _select_round_robin_key_locked(
        self,
        base_key: tuple[Language, str],
        keys: list[LspRuntimeKey],
    ) -> LspRuntimeKey:
        """동일 언어/레포의 실행 중 서버에서 RR 선택을 수행한다."""
        index = self._round_robin_cursor.get(base_key, 0)
        if len(keys) == 0:
            raise DaemonError(ErrorContext(code="ERR_LSP_UNAVAILABLE", message="사용 가능한 LSP 인스턴스가 없습니다"))
        selected = keys[index % len(keys)]
        self._round_robin_cursor[base_key] = (index + 1) % len(keys)
        return selected

    def _start_or_wait_for_slot(
        self,
        language: Language,
        repo_root: str,
        slot: int,
        wait_timeout_sec: float,
        request_kind: str,
    ) -> SolidLanguageServer:
        """동일 슬롯의 중복 기동을 방지하며 LSP 서버를 시작하거나 기존 기동을 기다린다."""
        key = LspRuntimeKey(language=language, repo_root=repo_root, slot=slot)
        while True:
            with self._perf_tracer.span(
                "start_or_wait.lock_check",
                phase="lsp_hub",
                language=language.value,
                repo_root=repo_root,
                request_kind=request_kind,
                slot=slot,
            ):
                with self._lock:
                    existing = self._instances.get(key)
                    if existing is not None and existing.server.server.is_running():
                        existing.last_used_at = self._clock()
                        return existing.server
                    if existing is not None:
                        self._cleanup_not_running_entry_locked(key=key, entry=existing)
                    start_event = self._starting_events.get(key)
                    if start_event is None:
                        self._evict_lru_if_needed_locked()
                        start_event = threading.Event()
                        self._starting_events[key] = start_event
                        owner = True
                    else:
                        owner = False
            if owner:
                try:
                    started_at = self._clock()
                    with self._perf_tracer.span(
                        "start_or_wait.start_semaphore_wait",
                        phase="lsp_hub",
                        language=language.value,
                        repo_root=repo_root,
                        request_kind=request_kind,
                        slot=slot,
                    ):
                        self._start_semaphore.acquire()
                    waited_ms = max(0.0, float(self._clock() - started_at) * 1000.0)
                    with self._lock:
                        self._start_semaphore_wait_ms_total += waited_ms
                    try:
                        with self._perf_tracer.span(
                            "start_or_wait.create_and_start_server",
                            phase="lsp_hub",
                            language=language.value,
                            repo_root=repo_root,
                            request_kind=request_kind,
                            slot=slot,
                        ):
                            started = self._create_and_start_server(language=language, repo_root=repo_root)
                    finally:
                        self._start_semaphore.release()
                except (DaemonError, RuntimeError, OSError, ValueError, TypeError, AssertionError) as exc:
                    with self._lock:
                        event = self._starting_events.pop(key, None)
                        if event is not None:
                            event.set()
                    if isinstance(exc, DaemonError):
                        raise
                    err_code, err_message = _classify_lsp_start_exception(exc=exc, language=language, repo_root=repo_root)
                    raise DaemonError(ErrorContext(code=err_code, message=err_message)) from exc
                with self._lock:
                    self._instances[key] = LspRuntimeEntry(server=started, last_used_at=self._clock())
                    event = self._starting_events.pop(key, None)
                    if event is not None:
                        event.set()
                return started
            with self._perf_tracer.span(
                "start_or_wait.wait_for_owner_start",
                phase="lsp_hub",
                language=language.value,
                repo_root=repo_root,
                request_kind=request_kind,
                slot=slot,
                wait_timeout_sec=wait_timeout_sec,
            ):
                wait_completed = start_event.wait(timeout=wait_timeout_sec)
            if not wait_completed:
                if request_kind == "interactive":
                    with self._lock:
                        self._interactive_timeout_count += 1
                raise DaemonError(
                    ErrorContext(
                        code="ERR_LSP_INTERACTIVE_TIMEOUT" if request_kind == "interactive" else "ERR_LSP_START_TIMEOUT",
                        message=f"LSP 서버 기동 대기 시간이 초과되었습니다: {language.value}@{repo_root} (kind={request_kind})",
                    )
                )

    def _normalize_request_kind(self, request_kind: str) -> str:
        """요청 종류 값을 정규화한다."""
        if request_kind.strip().lower() == "interactive":
            return "interactive"
        return "indexing"

    def _reserved_slots_for_key(self, base_key: tuple[Language, str]) -> int:
        """키별 인터랙티브 예약 슬롯 수를 계산한다."""
        max_slots = self._max_instances_for_key(base_key)
        if max_slots <= 1:
            return 0
        return max(0, min(self._interactive_reserved_slots_per_repo_language, max_slots - 1))

    def _is_slot_allowed_for_kind(self, base_key: tuple[Language, str], slot: int, request_kind: str) -> bool:
        """요청 종류에 따라 슬롯 사용 가능 여부를 판정한다."""
        if request_kind == "interactive":
            return True
        max_slots = self._max_instances_for_key(base_key)
        reserved = self._reserved_slots_for_key(base_key)
        indexing_ceiling = max(1, max_slots - reserved)
        return slot < indexing_ceiling

    def _first_allowed_slot(self, base_key: tuple[Language, str], request_kind: str) -> int:
        """요청 종류별 기본 시작 슬롯을 반환한다."""
        max_slots = self._max_instances_for_key(base_key)
        for slot in range(max_slots):
            if self._is_slot_allowed_for_kind(base_key=base_key, slot=slot, request_kind=request_kind):
                return slot
        if request_kind == "interactive":
            self._interactive_rejected_count += 1
        raise DaemonError(
            ErrorContext(
                code="ERR_LSP_ACQUIRE_REJECTED",
                message=f"요청 종류({request_kind})에 허용된 LSP 슬롯이 없습니다: {base_key[0].value}@{base_key[1]}",
            )
        )

    def _create_and_start_server(self, language: Language, repo_root: str) -> SolidLanguageServer:
        """락 밖에서 단일 LSP 서버를 생성/시작한다."""
        # NuGet/HTTPS 다운로드가 필요한 LSP가 인증서 검증 실패로 중단되지 않도록 기본 CA 번들을 주입한다.
        if certifi is not None:
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        with self._perf_tracer.span(
            "create_and_start.resolve_runtime_context",
            phase="lsp_hub",
            language=language.value,
            repo_root=repo_root,
        ):
            runtime_context = self._runtime_broker.resolve(language)
        attempts = self._resolve_start_attempt_envs(
            language=language,
            repo_root=repo_root,
            base_env_overrides=runtime_context.env_overrides,
        )
        last_exc: Exception | None = None
        for attempt_index, attempt_env in enumerate(attempts):
            ls: SolidLanguageServer | None = None
            try:
                with self._temporary_process_env(attempt_env):
                    config = LanguageServerConfig(code_language=language)
                    settings = SolidLSPSettings()
                    settings.ls_specific_settings[language] = {
                        "open_file_buffer_idle_ttl_sec": self._file_buffer_idle_ttl_sec,
                        "open_file_buffer_max_open": self._file_buffer_max_open,
                    }
                    with self._perf_tracer.span(
                        "create_and_start.server_create",
                        phase="lsp_hub",
                        language=language.value,
                        repo_root=repo_root,
                    ):
                        ls = SolidLanguageServer.create(
                            config=config,
                            repository_root_path=repo_root,
                            timeout=self._request_timeout_sec,
                            solidlsp_settings=settings,
                        )
                    with self._perf_tracer.span(
                        "create_and_start.server_start",
                        phase="lsp_hub",
                        language=language.value,
                        repo_root=repo_root,
                    ):
                        ls.start()
                    if not hasattr(ls, "started"):
                        setattr(ls, "started", True)
                    if attempt_index > 0:
                        log.warning(
                            "LSP auto-fallback success (language=%s, repo=%s, attempt=%d/%d)",
                            language.value,
                            repo_root,
                            attempt_index + 1,
                            len(attempts),
                        )
                    return ls
            except (ImportError, RuntimeError, OSError, ValueError, TypeError, AssertionError) as exc:
                last_exc = exc
                if ls is not None:
                    try:
                        ls.stop()
                    except (RuntimeError, OSError, ValueError):
                        log.debug(
                            "LSP stop failed during fallback cleanup (language=%s, repo=%s)",
                            language.value,
                            repo_root,
                            exc_info=True,
                        )
                if attempt_index + 1 < len(attempts):
                    log.warning(
                        "LSP start failed, retrying with fallback profile (language=%s, repo=%s, attempt=%d/%d, error=%s)",
                        language.value,
                        repo_root,
                        attempt_index + 1,
                        len(attempts),
                        exc,
                    )
                    continue
                break
        assert last_exc is not None
        err_code, err_message = _classify_lsp_start_exception(exc=last_exc, language=language, repo_root=repo_root)
        raise DaemonError(ErrorContext(code=err_code, message=err_message)) from last_exc

    def _resolve_start_attempt_envs(
        self,
        *,
        language: Language,
        repo_root: str,
        base_env_overrides: dict[str, str],
    ) -> list[dict[str, str]]:
        """언어/프로젝트별 LSP 시작 프로파일 시퀀스를 반환한다."""
        attempts: list[dict[str, str]] = [dict(base_env_overrides)]
        if language != Language.JAVA:
            return attempts
        explicit = os.getenv("SARI_JDTLS_GRADLE_WRAPPER_FIRST", "").strip().lower()
        if explicit in {"0", "false", "no", "off", "1", "true", "yes", "on"}:
            return attempts
        wrapper_props = Path(repo_root) / "gradle" / "wrapper" / "gradle-wrapper.properties"
        if not wrapper_props.exists():
            return attempts
        retry_env = dict(base_env_overrides)
        retry_env["SARI_JDTLS_GRADLE_WRAPPER_FIRST"] = "0"
        attempts.append(retry_env)
        return attempts

    @contextmanager
    def _temporary_process_env(self, env_overrides: dict[str, str]):
        """LSP 시작 구간에만 프로세스 환경을 임시 주입한다."""
        if len(env_overrides) == 0:
            yield
            return
        with self._environment_patch_lock:
            backup: dict[str, str | None] = {}
            for key, value in env_overrides.items():
                backup[key] = os.environ.get(key)
                os.environ[key] = value
            try:
                yield
            finally:
                for key, previous in backup.items():
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous

    def _idle_cleanup_loop(self) -> None:
        """유휴 인스턴스를 주기적으로 정리한다."""
        while not self._stop_cleanup.wait(self._idle_cleanup_interval_sec):
            try:
                with self._lock:
                    self._evict_idle_locked(self._clock())
            except (DaemonError, RuntimeError, OSError, ValueError) as exc:
                log.warning("LSP idle cleanup 실패: %s", exc)

    def _stop_server_with_timeout(self, server: SolidLanguageServer) -> None:
        """LSP stop 호출이 장시간 블로킹될 때 타임아웃으로 중단시킨다."""
        error_box: list[BaseException] = []
        done = threading.Event()

        def _runner() -> None:
            try:
                server.stop()
            except (RuntimeError, OSError, ValueError) as exc:  # pragma: no cover - stop 구현 예외 경계
                error_box.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=_runner, name="sari-lsp-stop-guard", daemon=True)
        worker.start()
        finished = done.wait(timeout=self._stop_timeout_sec)
        if not finished:
            self._stop_timeout_count += 1
            self._force_kill_server_process(server)
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_STOP_TIMEOUT",
                    message="LSP stop 타임아웃으로 인스턴스 정리를 완료하지 못했습니다",
                )
            )
        if len(error_box) > 0:
            first_error = error_box[0]
            if isinstance(first_error, (RuntimeError, OSError, ValueError)):
                raise first_error

    def _force_kill_server_process(self, server: SolidLanguageServer) -> None:
        """stop 타임아웃 시 LSP 하위 프로세스를 강제 종료한다."""
        handler = getattr(server, "server", None)
        process = getattr(handler, "process", None)
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        except OSError:
            pgid = None
        if isinstance(pgid, int) and pgid > 0:
            try:
                self._forced_kill_count += 1
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                log.warning("LSP killpg(SIGTERM) 실패: pid=%s pgid=%s", pid, pgid)
            time.sleep(0.1)
            try:
                os.killpg(pgid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except OSError:
                log.warning("LSP killpg(SIGKILL) 실패: pid=%s pgid=%s", pid, pgid)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            log.warning("LSP kill(SIGTERM) 실패: pid=%s", pid)
        time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            return


def _classify_lsp_start_exception(*, exc: BaseException, language: Language, repo_root: str) -> tuple[str, str]:
    """LSP 시작 실패 원인을 분류해 구체적인 오류 코드/메시지로 변환한다."""
    message = str(exc)
    exc_type = type(exc).__name__
    lowered = message.lower()
    if isinstance(exc, FileNotFoundError) or "command not found" in lowered or "no such file or directory" in lowered:
        return (
            "ERR_LSP_SERVER_MISSING",
            f"LSP 서버 실행 파일을 찾을 수 없습니다: {language.value}@{repo_root} ({exc_type}: {message})",
        )
    if isinstance(exc, PermissionError) or "permission denied" in lowered:
        return (
            "ERR_LSP_SERVER_SPAWN_FAILED",
            f"LSP 서버 실행 권한/스폰 실패: {language.value}@{repo_root} ({exc_type}: {message})",
        )
    if "version" in lowered and ("java" in lowered or "runtime" in lowered):
        return (
            "ERR_RUNTIME_MISMATCH",
            f"LSP 런타임 불일치: {language.value}@{repo_root} ({exc_type}: {message})",
        )
    return (
        "ERR_LSP_UNAVAILABLE",
        f"LSP 서버를 시작하지 못했습니다: {language.value}@{repo_root} ({exc_type}: {message})",
    )
