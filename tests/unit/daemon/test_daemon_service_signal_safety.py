"""데몬 시그널 전파의 안전 규칙을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema
from sari.services.daemon.service import DaemonService


def _build_service(tmp_path: Path) -> DaemonService:
    """테스트용 DaemonService를 생성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    config = AppConfig(db_path=db_path, host="127.0.0.1", preferred_port=9310, max_port_scan=5, stop_grace_sec=2)
    return DaemonService(config=config, runtime_repo=RuntimeRepository(db_path))


def test_signal_process_tree_skips_killpg_for_same_process_group(monkeypatch, tmp_path: Path) -> None:
    """현재 프로세스 그룹과 동일하면 killpg를 호출하지 않아야 한다."""
    service = _build_service(tmp_path)
    target_pid = 4242
    target_group = 777

    recorded_kill: list[tuple[int, int]] = []
    recorded_killpg: list[tuple[int, int]] = []

    monkeypatch.setattr("sari.services.daemon.service.os.getpgid", lambda _pid: target_group)
    monkeypatch.setattr("sari.services.daemon.service.os.getpgrp", lambda: target_group)
    monkeypatch.setattr("sari.services.daemon.service.os.kill", lambda pid, sig: recorded_kill.append((pid, int(sig))))
    monkeypatch.setattr(
        "sari.services.daemon.service.os.killpg",
        lambda pgid, sig: recorded_killpg.append((pgid, int(sig))),
    )

    import signal

    service._signal_process_tree(target_pid, signal.SIGTERM)

    assert recorded_kill == [(target_pid, int(signal.SIGTERM))]
    assert recorded_killpg == []
