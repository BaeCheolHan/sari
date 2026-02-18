"""DaemonService와 daemon registry 연동을 검증한다."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from sari.core.config import AppConfig
from sari.core.models import DaemonRuntimeDTO, WorkspaceDTO
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.daemon_service import DaemonService


class _DummyProcess:
    """테스트용 subprocess 결과 객체."""

    def __init__(self, pid: int) -> None:
        """PID만 보관한다."""
        self.pid = pid


def test_daemon_service_start_registers_registry_entry(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """daemon start 이후 registry 엔트리가 생성되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path="/repo/demo", name="demo", indexed_at=None, is_active=True))
    runtime_repo = RuntimeRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)

    service = DaemonService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=1,
            stop_grace_sec=1,
        ),
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )

    monkeypatch.setattr(service, "_is_port_free", lambda host, port: True)
    monkeypatch.setattr(service, "_clear_stale_runtime_if_needed", lambda: None)
    monkeypatch.setattr(service, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "sari.services.daemon_service.subprocess.Popen",
        lambda *args, **kwargs: _DummyProcess(pid=43210),
    )

    runtime = service.start(run_mode="dev")
    assert runtime.pid == 43210
    entries = registry_repo.list_all()
    assert len(entries) == 1
    assert entries[0].pid == 43210
    assert entries[0].workspace_root == "/repo/demo"


def test_daemon_service_stop_removes_registry_entry(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """daemon stop 완료 후 registry 엔트리가 제거되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    runtime_repo = RuntimeRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)

    service = DaemonService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=1,
            stop_grace_sec=1,
        ),
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )

    monkeypatch.setattr(service, "_is_port_free", lambda host, port: True)
    monkeypatch.setattr(service, "_clear_stale_runtime_if_needed", lambda: None)
    monkeypatch.setattr(service, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "sari.services.daemon_service.subprocess.Popen",
        lambda *args, **kwargs: _DummyProcess(pid=43211),
    )
    monkeypatch.setattr("sari.services.daemon_service.os.kill", lambda pid, sig: None)

    runtime = service.start(run_mode="dev")
    assert runtime.pid == 43211
    assert len(registry_repo.list_all()) == 1

    service.stop()
    assert len(registry_repo.list_all()) == 0
    assert runtime_repo.get_runtime() is None


def test_daemon_service_stop_signals_process_group(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """daemon stop 시 프로세스 그룹에도 종료 신호를 전달해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    runtime_repo = RuntimeRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)

    service = DaemonService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=1,
            stop_grace_sec=1,
        ),
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )

    monkeypatch.setattr(service, "_is_port_free", lambda host, port: True)
    monkeypatch.setattr(service, "_clear_stale_runtime_if_needed", lambda: None)
    monkeypatch.setattr(service, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "sari.services.daemon_service.subprocess.Popen",
        lambda *args, **kwargs: _DummyProcess(pid=43212),
    )

    called_signals: list[tuple[str, int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        called_signals.append(("pid", pid, sig))

    def _fake_getpgid(pid: int) -> int:
        return pid

    def _fake_killpg(pgid: int, sig: int) -> None:
        called_signals.append(("pgid", pgid, sig))

    monkeypatch.setattr("sari.services.daemon_service.os.kill", _fake_kill)
    monkeypatch.setattr("sari.services.daemon_service.os.getpgid", _fake_getpgid)
    monkeypatch.setattr("sari.services.daemon_service.os.killpg", _fake_killpg)

    runtime = service.start(run_mode="dev")
    assert runtime.pid == 43212

    service.stop()

    assert ("pid", 43212, 15) in called_signals
    assert ("pgid", 43212, 15) in called_signals


def test_clear_stale_runtime_kills_process_group(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """stale 런타임 정리 시 프로세스 그룹 강제 종료가 호출되어야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    runtime_repo = RuntimeRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)

    service = DaemonService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=1,
            stop_grace_sec=1,
            daemon_stale_timeout_sec=1,
        ),
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )

    runtime = DaemonRuntimeDTO(
        pid=54321,
        host="127.0.0.1",
        port=47777,
        state="running",
        started_at="1970-01-01T00:00:00+00:00",
        session_count=0,
        last_heartbeat_at="1970-01-01T00:00:00+00:00",
        last_exit_reason=None,
    )
    runtime_repo.upsert_runtime(runtime)

    monkeypatch.setattr(service, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr("sari.services.daemon_service.os.getpgid", lambda pid: pid)
    stale_calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr("sari.services.daemon_service.os.kill", lambda pid, sig: stale_calls.append(("pid", pid, sig)))
    monkeypatch.setattr("sari.services.daemon_service.os.killpg", lambda pgid, sig: stale_calls.append(("pgid", pgid, sig)))

    service._clear_stale_runtime_if_needed()

    assert ("pid", runtime.pid, 9) in stale_calls
    assert ("pgid", runtime.pid, 9) in stale_calls


def test_daemon_start_redirects_stdout_stderr_to_log_files(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """daemon start는 stdout/stderr를 파일로 리다이렉트해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    runtime_repo = RuntimeRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)

    service = DaemonService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=1,
            stop_grace_sec=1,
        ),
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )

    captured: dict[str, object] = {}

    def _fake_popen(*args, **kwargs) -> _DummyProcess:
        captured["stdout"] = kwargs.get("stdout")
        captured["stderr"] = kwargs.get("stderr")
        return _DummyProcess(pid=43213)

    monkeypatch.setattr(service, "_is_port_free", lambda host, port: True)
    monkeypatch.setattr(service, "_clear_stale_runtime_if_needed", lambda: None)
    monkeypatch.setattr(service, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr("sari.services.daemon_service.subprocess.Popen", _fake_popen)

    runtime = service.start(run_mode="dev")
    assert runtime.pid == 43213
    stdout_stream = captured["stdout"]
    stderr_stream = captured["stderr"]
    assert hasattr(stdout_stream, "name")
    assert hasattr(stderr_stream, "name")
    assert str(getattr(stdout_stream, "name")).endswith("daemon.stdout.log")
    assert str(getattr(stderr_stream, "name")).endswith("daemon.stderr.log")
