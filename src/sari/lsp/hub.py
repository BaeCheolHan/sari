"""Serena solidlsp 기반 LSP Hub를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Callable

from sari.core.language_registry import resolve_language_from_path
from sari.core.exceptions import DaemonError, ErrorContext
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


class LspHub:
    """언어별 LSP 인스턴스 생명주기를 관리한다."""

    def __init__(
        self,
        idle_timeout_sec: int = 900,
        max_instances: int = 32,
        max_instances_per_repo_language: int = 1,
        lsp_global_soft_limit: int = 0,
        hot_acquire_window_sec: float = 1.0,
        scale_out_hot_hits: int = 24,
        idle_cleanup_interval_sec: float = 5.0,
        stop_timeout_sec: float = 3.0,
        request_timeout_sec: float = 20.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """내부 인스턴스 캐시를 초기화한다."""
        self._instances: dict[LspRuntimeKey, LspRuntimeEntry] = {}
        self._idle_timeout_sec = max(1, idle_timeout_sec)
        self._max_instances = max(1, max_instances)
        self._max_instances_per_repo_language = max(1, min(2, max_instances_per_repo_language))
        self._lsp_global_soft_limit = max(0, lsp_global_soft_limit)
        self._hot_acquire_window_sec = max(0.1, hot_acquire_window_sec)
        self._scale_out_hot_hits = max(2, scale_out_hot_hits)
        self._round_robin_cursor: dict[tuple[Language, str], int] = {}
        self._last_acquire_at: dict[tuple[Language, str], float] = {}
        self._hot_acquire_hits: dict[tuple[Language, str], int] = {}
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._idle_cleanup_interval_sec = max(0.5, idle_cleanup_interval_sec)
        self._stop_timeout_sec = max(0.2, stop_timeout_sec)
        self._request_timeout_sec = max(0.1, request_timeout_sec)
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

    def resolve_language(self, file_path: str) -> Language:
        """파일 확장자로 언어를 결정한다."""
        resolved = resolve_language_from_path(file_path=file_path)
        if resolved is not None:
            return resolved
        raise DaemonError(ErrorContext(code="ERR_UNSUPPORTED_LANGUAGE", message="지원하지 않는 언어 확장자입니다"))

    def get_or_start(self, language: Language, repo_root: str) -> SolidLanguageServer:
        """언어/저장소 기준으로 LSP를 가져오거나 시작한다."""
        normalized_root = str(Path(repo_root).resolve())
        base_key = (language, normalized_root)
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
                    running_keys.append(key)
                    continue
                self._cleanup_not_running_entry_locked(key=key, entry=entry)

            should_scale_out = self._should_scale_out_locked(base_key=base_key, now=now, running_count=len(running_keys))
            if should_scale_out:
                try:
                    slot = self._next_slot_locked(language=language, repo_root=normalized_root)
                except DaemonError as exc:
                    if exc.context.code != "ERR_LSP_SLOT_EXHAUSTED" or len(running_keys) == 0:
                        raise
                    selected_key = self._select_round_robin_key_locked(base_key=base_key, keys=running_keys)
                    selected_entry = self._instances[selected_key]
                    selected_entry.last_used_at = now
                    self._last_acquire_at[base_key] = now
                    return selected_entry.server
                server = self._start_server_locked(language=language, repo_root=normalized_root, slot=slot, now=now)
                self._last_acquire_at[base_key] = now
                return server

            if len(running_keys) > 0:
                selected_key = self._select_round_robin_key_locked(base_key=base_key, keys=running_keys)
                selected_entry = self._instances[selected_key]
                selected_entry.last_used_at = now
                self._last_acquire_at[base_key] = now
                return selected_entry.server

            server = self._start_server_locked(language=language, repo_root=normalized_root, slot=0, now=now)
            self._last_acquire_at[base_key] = now
            return server

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
            while len(running_keys) < self._max_instances_per_repo_language:
                try:
                    slot = self._next_slot_locked(language=language, repo_root=normalized_root)
                except DaemonError as exc:
                    if exc.context.code == "ERR_LSP_SLOT_EXHAUSTED":
                        break
                    raise
                self._start_server_locked(language=language, repo_root=normalized_root, slot=slot, now=now)
                running_keys = []
                for key in self._runtime_keys_for_locked(language=language, repo_root=normalized_root):
                    entry = self._instances.get(key)
                    if entry is not None and entry.server.server.is_running():
                        running_keys.append(key)

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
            }

    def _evict_idle_locked(self, now: float) -> None:
        """idle timeout을 초과한 인스턴스를 정리한다."""
        evict_keys = [
            key
            for key, entry in self._instances.items()
            if (now - entry.last_used_at) >= float(self._idle_timeout_sec)
        ]
        for key in evict_keys:
            self._stop_entry_locked(key)

    def _evict_lru_if_needed_locked(self) -> None:
        """최대 인스턴스 수를 넘기기 전에 LRU 인스턴스를 정리한다."""
        while len(self._instances) >= self._max_instances:
            lru_key = min(self._instances.keys(), key=lambda key: self._instances[key].last_used_at)
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

    def _next_slot_locked(self, language: Language, repo_root: str) -> int:
        """새로운 인스턴스에 사용할 슬롯 번호를 계산한다."""
        used_slots = {key.slot for key in self._runtime_keys_for_locked(language=language, repo_root=repo_root)}
        for slot in range(self._max_instances_per_repo_language):
            if slot not in used_slots:
                return slot
        raise DaemonError(
            ErrorContext(
                code="ERR_LSP_SLOT_EXHAUSTED",
                message=f"LSP 슬롯이 모두 사용 중입니다: {language.value}@{repo_root}",
            )
        )

    def _should_scale_out_locked(self, base_key: tuple[Language, str], now: float, running_count: int) -> bool:
        """짧은 시간 내 재요청이 몰리면 동일 언어/레포 풀을 2개까지 확장한다."""
        self._record_hot_hit_locked(base_key=base_key, now=now)
        if running_count == 0:
            return True
        # 전역 소프트 상한을 넘기면 추가 scale-out을 차단한다.
        if self._lsp_global_soft_limit > 0 and len(self._instances) >= self._lsp_global_soft_limit:
            return False
        if running_count >= self._max_instances_per_repo_language:
            return False
        hits = self._hot_acquire_hits.get(base_key, 0)
        return hits >= self._scale_out_hot_hits

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

    def _start_server_locked(self, language: Language, repo_root: str, slot: int, now: float) -> SolidLanguageServer:
        """단일 LSP 서버를 생성하고 캐시에 등록한다."""
        self._evict_lru_if_needed_locked()
        # NuGet/HTTPS 다운로드가 필요한 LSP가 인증서 검증 실패로 중단되지 않도록 기본 CA 번들을 주입한다.
        if certifi is not None:
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        self._validate_runtime_requirements(language=language)
        self._ensure_user_tool_paths(language=language)
        try:
            config = LanguageServerConfig(code_language=language)
            settings = SolidLSPSettings()
            ls = SolidLanguageServer.create(
                config=config,
                repository_root_path=repo_root,
                timeout=self._request_timeout_sec,
                solidlsp_settings=settings,
            )
            ls.start()
            if not hasattr(ls, "started"):
                setattr(ls, "started", True)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, AssertionError) as exc:
            raise DaemonError(ErrorContext(code="ERR_LSP_UNAVAILABLE", message="LSP 서버를 시작하지 못했습니다")) from exc
        key = LspRuntimeKey(language=language, repo_root=repo_root, slot=slot)
        self._instances[key] = LspRuntimeEntry(server=ls, last_used_at=now)
        return ls

    def _validate_runtime_requirements(self, language: Language) -> None:
        """언어별 런타임 최소 요구사항을 사전 검증한다."""
        if language not in {Language.JAVA, Language.KOTLIN}:
            return
        try:
            result = subprocess.run(
                ["java", "-version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_RUNTIME_PROBE_FAILED",
                    message=f"Java 런타임 점검 실패: {exc}",
                )
            ) from exc
        version_text = f"{result.stderr}\n{result.stdout}"
        major = self._parse_java_major_version(version_text)
        if major is None:
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_RUNTIME_PROBE_FAILED",
                    message="Java 버전을 해석할 수 없습니다",
                )
            )
        if major < 17:
            raise DaemonError(
                ErrorContext(
                    code="ERR_LSP_RUNTIME_MISMATCH",
                    message=f"Java 17+ 런타임이 필요합니다(현재: {major})",
                )
            )

    def _parse_java_major_version(self, version_text: str) -> int | None:
        """`java -version` 출력에서 major 버전을 파싱한다."""
        match = re.search(r'version\s+"([^"]+)"', version_text)
        if match is None:
            return None
        raw_version = match.group(1).strip()
        if raw_version.startswith("1."):
            parts = raw_version.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
            return None
        major_text = raw_version.split(".")[0]
        if major_text.isdigit():
            return int(major_text)
        return None

    def _ensure_user_tool_paths(self, language: Language) -> None:
        """사용자 로컬 설치 경로를 PATH/런타임 변수에 보강한다."""
        home = str(Path.home())
        extra_paths: list[str] = []
        if language == Language.GO:
            extra_paths.append(f"{home}/go/bin")
        if language == Language.RUBY:
            extra_paths.extend([f"{home}/.gem/ruby/2.6.0/bin", "/opt/homebrew/lib/ruby/gems/4.0.0/bin", "/opt/homebrew/opt/ruby/bin"])
        if language == Language.PERL:
            extra_paths.append(f"{home}/perl5/bin")
            current_perl5 = os.environ.get("PERL5LIB", "").strip()
            perl_lib = f"{home}/perl5/lib/perl5"
            if len(current_perl5) == 0:
                os.environ["PERL5LIB"] = perl_lib
            elif perl_lib not in current_perl5.split(":"):
                os.environ["PERL5LIB"] = f"{perl_lib}:{current_perl5}"
        if len(extra_paths) == 0:
            return
        current_path = os.environ.get("PATH", "")
        parts = [part for part in current_path.split(":") if len(part) > 0]
        for path_item in reversed(extra_paths):
            if path_item not in parts and Path(path_item).exists():
                parts.insert(0, path_item)
        os.environ["PATH"] = ":".join(parts)

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
