"""데몬 수명주기 서비스를 구현한다."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import logging
import uuid
from typing import TextIO
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sari.core.config import AppConfig
from sari.core.exceptions import DaemonError, ErrorContext
from sari.core.models import DaemonRegistryEntryDTO, DaemonRuntimeDTO, now_iso8601_utc
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository

log = logging.getLogger(__name__)


class DaemonService:
    """데몬 시작/상태/종료 규칙을 담당한다."""

    def __init__(
        self,
        config: AppConfig,
        runtime_repo: RuntimeRepository,
        workspace_repo: WorkspaceRepository | None = None,
        registry_repo: DaemonRegistryRepository | None = None,
    ) -> None:
        """서비스 생성 시 설정과 저장소를 주입한다."""
        self._config = config
        self._runtime_repo = runtime_repo
        self._workspace_repo = workspace_repo
        self._registry_repo = registry_repo
        self._stale_alive_events = 0
        self._duplicate_guard_hits = 0

    def start(self, run_mode: str | None = None) -> DaemonRuntimeDTO:
        """데몬 프로세스를 백그라운드로 시작하고 런타임 상태를 저장한다."""
        previous_runtime = self._runtime_repo.get_runtime()
        next_generation = 1
        if previous_runtime is not None:
            next_generation = max(1, int(previous_runtime.owner_generation) + 1)
        self._clear_stale_runtime_if_needed()
        existing = self._runtime_repo.get_runtime()
        if existing is not None and self._is_pid_alive(existing.pid):
            self._duplicate_guard_hits += 1
            raise DaemonError(ErrorContext(code="ERR_DAEMON_ALREADY_RUNNING", message="이미 데몬이 실행 중입니다"))

        port = self._allocate_port(self._config.preferred_port, self._config.max_port_scan)
        command = [
            sys.executable,
            "-m",
            "sari.daemon_process",
            "--db-path",
            str(self._config.db_path),
            "--host",
            self._config.host,
            "--port",
            str(port),
        ]
        selected_run_mode = self._config.run_mode if run_mode is None else run_mode
        if selected_run_mode not in {"dev", "prod"}:
            raise DaemonError(ErrorContext(code="ERR_INVALID_RUN_MODE", message="run_mode는 dev 또는 prod여야 합니다"))
        command.extend(["--run-mode", selected_run_mode])
        # 패키지 이관 후에도 프로젝트 src 루트를 정확히 가리켜야 한다.
        src_root = str(Path(__file__).resolve().parents[3])
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src_root if existing_pythonpath == "" else f"{src_root}:{existing_pythonpath}"
        # 데몬 서비스가 백그라운드 분리 실행임을 자식 프로세스에 명시한다.
        env["SARI_DAEMON_DETACHED"] = "1"
        stdout_stream, stderr_stream = self._open_daemon_log_streams()
        try:
            process = subprocess.Popen(
                command,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=True,
                env=env,
            )
        finally:
            # 자식 프로세스가 fd를 복제하므로 부모에서 즉시 닫아도 안전하다.
            stdout_stream.close()
            stderr_stream.close()
        now = now_iso8601_utc()
        lease_expires_at = self._compute_lease_expires_at(now)
        runtime = DaemonRuntimeDTO(
            pid=process.pid,
            host=self._config.host,
            port=port,
            state="running",
            started_at=now,
            session_count=0,
            last_heartbeat_at=now,
            last_exit_reason=None,
            lease_token=str(uuid.uuid4()),
            owner_generation=next_generation,
            updated_at=now,
            lease_expires_at=lease_expires_at,
        )
        self._runtime_repo.upsert_runtime(runtime)
        self._runtime_repo.reset_session_count()
        self._register_registry_entry(runtime)
        return runtime

    def ensure_running(self, run_mode: str | None = None) -> DaemonRuntimeDTO:
        """데몬이 이미 실행 중이면 attach하고, 아니면 시작한다."""
        self._clear_stale_runtime_if_needed()
        existing = self._runtime_repo.get_runtime()
        if existing is not None and self._is_pid_alive(existing.pid):
            if self._is_attachable_registry_state(existing.pid):
                self._touch_registry(existing.pid)
                return existing
            self.stop()
        return self.start(run_mode=run_mode)

    def status(self) -> DaemonRuntimeDTO | None:
        """현재 데몬 상태를 조회한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return None
        health = self.describe_runtime_health(runtime)
        if health["health_state"] == "dead":
            self._record_registry_health(pid=runtime.pid, ok=False, error_message="process dead")
            self._remove_registry_by_pid(runtime.pid)
            self._runtime_repo.clear_runtime()
            return None
        if health["health_state"] in {"stale", "degraded"}:
            # 일시적 DB lock/지연으로 heartbeat가 stale해도 프로세스가 살아있다면
            # 런타임 레코드를 제거하지 않는다(중복 데몬 기동/불필요한 kill 방지).
            self._record_registry_health(pid=runtime.pid, ok=False, error_message=str(health["status_reason"]))
            if health["health_state"] == "stale":
                self._stale_alive_events += 1
            return runtime
        self._touch_registry(runtime.pid)
        return runtime

    def stop(self) -> None:
        """실행 중인 데몬을 종료한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            raise DaemonError(ErrorContext(code="ERR_DAEMON_NOT_RUNNING", message="실행 중인 데몬이 없습니다"))

        try:
            self._signal_process_tree(runtime.pid, signal.SIGTERM)
        except ProcessLookupError as exc:
            self._remove_registry_by_pid(runtime.pid)
            self._runtime_repo.clear_runtime()
            raise DaemonError(ErrorContext(code="ERR_DAEMON_NOT_FOUND", message="데몬 프로세스를 찾을 수 없습니다")) from exc

        deadline = time.time() + self._config.stop_grace_sec
        while time.time() < deadline:
            if not self._is_pid_alive(runtime.pid):
                self._runtime_repo.mark_exit_reason(runtime.pid, "NORMAL_SHUTDOWN", now_iso8601_utc())
                self._remove_registry_by_pid(runtime.pid)
                self._runtime_repo.clear_runtime()
                return
            time.sleep(0.1)

        try:
            self._signal_process_tree(runtime.pid, signal.SIGKILL)
        except ProcessLookupError:
            # 강제 종료 시점에 이미 프로세스가 종료된 경우를 기록한다.
            log.debug("강제 종료 시점에 데몬 프로세스가 이미 종료됨(pid=%s)", runtime.pid)
        self._runtime_repo.mark_exit_reason(runtime.pid, "FORCE_KILLED", now_iso8601_utc())
        self._remove_registry_by_pid(runtime.pid)
        self._runtime_repo.clear_runtime()

    def _allocate_port(self, preferred_port: int, max_scan: int) -> int:
        """사용 가능한 포트를 탐색해 반환한다."""
        for offset in range(max_scan + 1):
            port = preferred_port + offset
            if self._is_port_free(self._config.host, port):
                return port
        raise DaemonError(ErrorContext(code="ERR_PORT_EXHAUSTED", message="사용 가능한 포트를 찾지 못했습니다"))

    def _is_port_free(self, host: str, port: int) -> bool:
        """포트 사용 가능 여부를 검사한다."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return False
        return True

    def _is_pid_alive(self, pid: int) -> bool:
        """PID가 살아있는지 확인한다."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        stat = self._read_process_stat(pid)
        if stat.startswith("Z"):
            return False
        return True

    def _read_process_stat(self, pid: int) -> str:
        """프로세스 상태 문자열(ps stat)을 조회한다."""
        process = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        if process.returncode != 0:
            return ""
        return process.stdout.strip()

    def _read_process_command(self, pid: int) -> str:
        """프로세스 실행 커맨드를 조회한다."""
        process = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        if process.returncode != 0:
            return ""
        return process.stdout.strip()

    def _is_expected_daemon_process(self, pid: int) -> bool:
        """PID가 우리 daemon_process인지 확인한다."""
        command = self._read_process_command(pid)
        return "sari.daemon_process" in command

    def _is_runtime_stale(self, last_heartbeat_at: str) -> bool:
        """heartbeat 기준 stale 상태를 판정한다."""
        try:
            last = datetime.fromisoformat(last_heartbeat_at)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._config.daemon_stale_timeout_sec)
        return last < cutoff

    def _is_lease_valid(self, lease_expires_at: str | None) -> bool:
        """lease 만료 시각 기준으로 유효성을 판정한다."""
        if lease_expires_at is None or lease_expires_at.strip() == "":
            return True
        try:
            lease_until = datetime.fromisoformat(lease_expires_at)
        except ValueError:
            return False
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        return lease_until >= datetime.now(timezone.utc)

    def _compute_lease_expires_at(self, base_iso: str) -> str:
        """기준 시각에서 lease 만료 시각을 계산한다."""
        base = datetime.fromisoformat(base_iso)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        ttl_sec = max(5, int(self._config.daemon_stale_timeout_sec))
        return (base + timedelta(seconds=ttl_sec)).isoformat()

    def _clear_stale_runtime_if_needed(self) -> None:
        """stale heartbeat 런타임 레코드를 정리한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return
        if not self._is_runtime_stale(runtime.last_heartbeat_at):
            return
        if self._is_pid_alive(runtime.pid):
            if not self._is_expected_daemon_process(runtime.pid):
                self._remove_registry_by_pid(runtime.pid)
                self._runtime_repo.clear_runtime()
                return
            # stale heartbeat만으로 살아있는 프로세스를 종료하지 않는다.
            self._stale_alive_events += 1
            return
        self._remove_registry_by_pid(runtime.pid)
        self._runtime_repo.clear_runtime()

    def describe_runtime_health(self, runtime: DaemonRuntimeDTO) -> dict[str, object]:
        """런타임 상태를 다중 신호로 판정해 근거를 반환한다."""
        pid_alive = self._is_pid_alive(runtime.pid)
        stale = self._is_runtime_stale(runtime.last_heartbeat_at)
        lease_valid = self._is_lease_valid(runtime.lease_expires_at)
        reason = "running"
        state = "running"
        if not pid_alive:
            state = "dead"
            reason = "process_dead"
        elif stale:
            state = "stale"
            reason = "heartbeat_stale_but_pid_alive"
        elif not lease_valid:
            state = "degraded"
            reason = "lease_invalid_but_pid_alive"
        return {
            "health_state": state,
            "status_reason": reason,
            "pid_alive": pid_alive,
            "lease_valid": lease_valid,
        }

    def get_guard_counters(self) -> dict[str, int]:
        """데몬 중복/오탐 방지 카운터를 반환한다."""
        return {
            "stale_alive_events": int(self._stale_alive_events),
            "daemon_duplicate_guard_hits": int(self._duplicate_guard_hits),
        }

    def _signal_process_tree(self, pid: int, sig: signal.Signals) -> None:
        """대상 PID와 같은 프로세스 그룹에 동일 시그널을 전파한다."""
        os.kill(pid, sig)
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        except OSError:
            return
        # 현재 프로세스와 같은 그룹이면 자기 자신까지 종료될 수 있으므로 killpg를 생략한다.
        try:
            current_pgid = os.getpgrp()
        except OSError:
            current_pgid = -1
        if pgid == current_pgid:
            return
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except OSError:
            return

    def _open_daemon_log_streams(self) -> tuple[TextIO, TextIO]:
        """데몬 stdout/stderr 리다이렉트를 위한 로그 파일 스트림을 연다."""
        log_dir = self._config.db_path.parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_stream = (log_dir / "daemon.stdout.log").open(mode="a", encoding="utf-8")
            stderr_stream = (log_dir / "daemon.stderr.log").open(mode="a", encoding="utf-8")
        except OSError as exc:
            raise DaemonError(
                ErrorContext(code="ERR_DAEMON_LOG_OPEN_FAILED", message="데몬 로그 파일을 열지 못했습니다")
            ) from exc
        return stdout_stream, stderr_stream

    def _register_registry_entry(self, runtime: DaemonRuntimeDTO) -> None:
        """런타임 상태를 daemon registry에 등록한다."""
        if self._registry_repo is None:
            return
        workspace_root = self._resolve_registry_workspace_root()
        daemon_id = self._build_daemon_id(runtime)
        entry = DaemonRegistryEntryDTO(
            daemon_id=daemon_id,
            host=runtime.host,
            port=runtime.port,
            pid=runtime.pid,
            workspace_root=workspace_root,
            protocol="http",
            started_at=runtime.started_at,
            last_seen_at=runtime.last_heartbeat_at,
            is_draining=False,
        )
        self._registry_repo.upsert(entry)

    def _touch_registry(self, pid: int) -> None:
        """레지스트리 last_seen을 갱신한다."""
        if self._registry_repo is None:
            return
        daemon_id = self._find_registry_daemon_id_by_pid(pid)
        if daemon_id is None:
            return
        now = now_iso8601_utc()
        self._registry_repo.touch(daemon_id=daemon_id, seen_at=now)
        self._registry_repo.record_health_result(daemon_id=daemon_id, ok=True, health_at=now)

    def _remove_registry_by_pid(self, pid: int) -> None:
        """종료된 PID의 레지스트리 엔트리를 제거한다."""
        if self._registry_repo is None:
            return
        self._registry_repo.remove_by_pid(pid=pid)

    def _record_registry_health(self, pid: int, ok: bool, error_message: str | None = None) -> None:
        """PID 기준으로 레지스트리 헬스 상태를 기록한다."""
        if self._registry_repo is None:
            return
        daemon_id = self._find_registry_daemon_id_by_pid(pid)
        if daemon_id is None:
            return
        self._registry_repo.record_health_result(
            daemon_id=daemon_id,
            ok=ok,
            health_at=now_iso8601_utc(),
            error_message=error_message,
        )

    def _resolve_registry_workspace_root(self) -> str:
        """레지스트리 엔트리에 사용할 워크스페이스 루트를 결정한다."""
        if self._workspace_repo is None:
            return "__global__"
        items = self._workspace_repo.list_all()
        if len(items) == 0:
            return "__global__"
        return items[0].path

    def _build_daemon_id(self, runtime: DaemonRuntimeDTO) -> str:
        """레지스트리용 daemon_id를 구성한다."""
        started_key = runtime.started_at.replace(":", "").replace("-", "")
        return f"daemon-{runtime.pid}-{started_key}"

    def _find_registry_daemon_id_by_pid(self, pid: int) -> str | None:
        """PID로 등록된 daemon_id를 찾는다."""
        if self._registry_repo is None:
            return None
        for item in self._registry_repo.list_all():
            if item.pid == pid:
                return item.daemon_id
        return None

    def _is_attachable_registry_state(self, pid: int) -> bool:
        """현재 PID가 attach 가능한 registry 상태인지 판단한다."""
        if self._registry_repo is None:
            return True
        for item in self._registry_repo.list_all():
            if item.pid != pid:
                continue
            if item.is_draining:
                return False
            if item.deployment_state != "ACTIVE":
                return False
            return True
        return True
