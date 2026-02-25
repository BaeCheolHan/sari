"""데몬/워커 생명주기 E2E 시나리오를 검증한다."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from sari.core.config import AppConfig
from sari.core.exceptions import DaemonError
from sari.core.models import DaemonRuntimeDTO
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema
from sari.services.daemon.service import DaemonService


def _pick_free_port() -> int:
    """OS가 할당한 임시 가용 포트를 얻는다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_daemon_service(db_path: Path, preferred_port: int, stop_grace_sec: int = 2) -> tuple[DaemonService, RuntimeRepository]:
    """테스트용 데몬 서비스와 런타임 저장소를 생성한다."""
    config = AppConfig(
        db_path=db_path,
        host="127.0.0.1",
        preferred_port=preferred_port,
        max_port_scan=20,
        stop_grace_sec=stop_grace_sec,
    )
    runtime_repo = RuntimeRepository(db_path)
    return DaemonService(config=config, runtime_repo=runtime_repo), runtime_repo


def _wait_http_ready(url: str, timeout_sec: float = 5.0) -> None:
    """HTTP 서버 준비 완료까지 대기한다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError("HTTP 서버 준비 시간이 초과되었습니다")


def _wait_pid_exit(pid: int, timeout_sec: float = 10.0) -> None:
    """PID 종료를 timeout 내에 대기한다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        can_wait_child = True
        try:
            waited_pid, _ = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                return
        except ChildProcessError:
            can_wait_child = False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        ps = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        stat = ps.stdout.strip()
        if stat == "" or stat.startswith("Z"):
            return
        time.sleep(0.1)
    raise AssertionError(f"pid did not exit in time: {pid}")


def _wait_pid_stopped(pid: int, timeout_sec: float = 5.0) -> None:
    """PID가 중지(T) 상태로 전이될 때까지 대기한다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        ps = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        stat = ps.stdout.strip()
        if stat.startswith("T"):
            return
        time.sleep(0.1)
    raise AssertionError(f"pid did not stop in time: {pid}")


def test_stale_runtime_cleanup_kills_stale_pid_on_start(tmp_path: Path) -> None:
    """stale runtime가 살아있으면 start 시 정리/강제종료되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    daemon_service, runtime_repo = _build_daemon_service(db_path=db_path, preferred_port=_pick_free_port())

    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        runtime_repo.upsert_runtime(
            DaemonRuntimeDTO(
                pid=int(sleeper.pid),
                host="127.0.0.1",
                port=47777,
                state="running",
                started_at="2026-02-15T00:00:00+00:00",
                session_count=0,
                last_heartbeat_at="2026-02-15T00:00:00+00:00",
                last_exit_reason=None,
            )
        )

        runtime = daemon_service.start()
        assert runtime.pid != sleeper.pid
        deadline = time.time() + 3.0
        while time.time() < deadline and sleeper.poll() is None:
            time.sleep(0.1)
        assert sleeper.poll() is not None
    finally:
        try:
            daemon_service.stop()
        except DaemonError as exc:
            assert exc.context.code in {"ERR_DAEMON_NOT_RUNNING", "ERR_DAEMON_NOT_FOUND"}


def test_orphan_daemon_self_terminates_and_records_exit_reason(tmp_path: Path) -> None:
    """부모가 종료되면 데몬은 고아 감지 후 자가종료해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    port = _pick_free_port()
    src_root = str((Path(__file__).resolve().parents[2] / "src"))

    launcher_code = r'''
import json
import os
import subprocess
import sys
from pathlib import Path
from sari.db.schema import init_schema

db_path = Path(sys.argv[1])
port = int(sys.argv[2])
init_schema(db_path)
env = os.environ.copy()
env.pop("SARI_DAEMON_DETACHED", None)
proc = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "sari.daemon_process",
        "--db-path",
        str(db_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--run-mode",
        "prod",
    ],
    env=env,
)
print(json.dumps({"pid": proc.pid}), flush=True)
'''

    env = os.environ.copy()
    env["PYTHONPATH"] = src_root if env.get("PYTHONPATH", "") == "" else f"{src_root}:{env['PYTHONPATH']}"
    launcher = subprocess.Popen(
        [sys.executable, "-c", launcher_code, str(db_path), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    out, err = launcher.communicate(timeout=10)
    assert launcher.returncode == 0, err
    pid = int(json.loads(out.strip())["pid"])

    _wait_pid_exit(pid, timeout_sec=15.0)

    runtime_repo = RuntimeRepository(db_path)
    latest = runtime_repo.get_latest_exit_event()
    assert latest is not None
    assert latest["exit_reason"] == "ORPHAN_SELF_TERMINATE"


def test_graceful_sigterm_records_normal_shutdown(tmp_path: Path) -> None:
    """graceful stop 경로에서 NORMAL_SHUTDOWN이 기록되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    daemon_service, runtime_repo = _build_daemon_service(db_path=db_path, preferred_port=_pick_free_port())

    daemon_service.start(run_mode="prod")
    daemon_service.stop()
    latest = runtime_repo.get_latest_exit_event()
    assert latest is not None
    assert latest["exit_reason"] == "NORMAL_SHUTDOWN"


def test_force_kill_fallback_records_force_killed(tmp_path: Path) -> None:
    """stop grace timeout 초과 시 FORCE_KILLED가 기록되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    daemon_service, runtime_repo = _build_daemon_service(
        db_path=db_path,
        preferred_port=_pick_free_port(),
        stop_grace_sec=1,
    )

    runtime = daemon_service.start(run_mode="prod")
    _wait_http_ready(f"http://{runtime.host}:{runtime.port}/health")
    os.kill(runtime.pid, signal.SIGSTOP)
    _wait_pid_stopped(runtime.pid)
    daemon_service.stop()

    latest = runtime_repo.get_latest_exit_event()
    assert latest is not None
    assert latest["exit_reason"] == "FORCE_KILLED"


def test_auto_loop_failure_stops_daemon_in_dev_mode(tmp_path: Path) -> None:
    """auto-loop 실패를 유도하면 dev 모드 데몬이 종료되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    daemon_service, runtime_repo = _build_daemon_service(db_path=db_path, preferred_port=_pick_free_port())

    os.environ["SARI_TEST_AUTO_LOOP_FAIL"] = "1"
    try:
        runtime = daemon_service.start(run_mode="dev")
        _wait_pid_exit(runtime.pid, timeout_sec=10.0)
        row = runtime_repo.get_runtime()
        assert row is not None
        assert row.last_exit_reason == "AUTO_LOOP_FAILURE"
    finally:
        os.environ.pop("SARI_TEST_AUTO_LOOP_FAIL", None)
