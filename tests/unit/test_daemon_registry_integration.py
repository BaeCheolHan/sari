"""DaemonService와 daemon registry 연동을 검증한다."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from sari.core.config import AppConfig
from sari.core.models import WorkspaceDTO
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
