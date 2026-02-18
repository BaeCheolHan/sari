"""데몬 수명주기 서비스를 구현한다."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import logging
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

    def start(self, run_mode: str | None = None) -> DaemonRuntimeDTO:
        """데몬 프로세스를 백그라운드로 시작하고 런타임 상태를 저장한다."""
        self._clear_stale_runtime_if_needed()
        existing = self._runtime_repo.get_runtime()
        if existing is not None and self._is_pid_alive(existing.pid):
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
        src_root = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = src_root if existing_pythonpath == "" else f"{src_root}:{existing_pythonpath}"
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
        runtime = DaemonRuntimeDTO(
            pid=process.pid,
            host=self._config.host,
            port=port,
            state="running",
            started_at=now_iso8601_utc(),
            session_count=0,
            last_heartbeat_at=now_iso8601_utc(),
            last_exit_reason=None,
        )
        self._runtime_repo.upsert_runtime(runtime)
        self._runtime_repo.reset_session_count()
        self._register_registry_entry(runtime)
        return runtime

    def status(self) -> DaemonRuntimeDTO | None:
        """현재 데몬 상태를 조회한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return None
        if self._is_runtime_stale(runtime.last_heartbeat_at):
            self._remove_registry_by_pid(runtime.pid)
            self._runtime_repo.clear_runtime()
            return None
        if not self._is_pid_alive(runtime.pid):
            self._remove_registry_by_pid(runtime.pid)
            self._runtime_repo.clear_runtime()
            return None
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

    def _clear_stale_runtime_if_needed(self) -> None:
        """stale heartbeat 런타임 레코드를 정리한다."""
        runtime = self._runtime_repo.get_runtime()
        if runtime is None:
            return
        if not self._is_runtime_stale(runtime.last_heartbeat_at):
            return
        if self._is_pid_alive(runtime.pid):
            try:
                self._signal_process_tree(runtime.pid, signal.SIGKILL)
            except ProcessLookupError:
                log.debug("stale runtime 정리 중 이미 프로세스 종료(pid=%s)", runtime.pid)
        self._remove_registry_by_pid(runtime.pid)
        self._runtime_repo.clear_runtime()

    def _signal_process_tree(self, pid: int, sig: signal.Signals) -> None:
        """대상 PID와 같은 프로세스 그룹에 동일 시그널을 전파한다."""
        os.kill(pid, sig)
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        except OSError:
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
        self._registry_repo.touch(daemon_id=daemon_id, seen_at=now_iso8601_utc())

    def _remove_registry_by_pid(self, pid: int) -> None:
        """종료된 PID의 레지스트리 엔트리를 제거한다."""
        if self._registry_repo is None:
            return
        self._registry_repo.remove_by_pid(pid=pid)

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
