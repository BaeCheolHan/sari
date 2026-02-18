"""HTTP 파이프라인 운영 엔드포인트를 검증한다."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.services.daemon_service import DaemonService
from sari.services.workspace_service import WorkspaceService


def _pick_free_port() -> int:
    """OS가 할당한 임시 가용 포트를 얻는다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ready(url: str, timeout_sec: float = 5.0) -> None:
    """HTTP 서버가 준비될 때까지 대기한다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError("HTTP 서버 준비 시간이 초과되었습니다")


def _read_json(url: str, method: str = "GET") -> dict[str, object]:
    """JSON 응답을 읽어 파싱한다."""
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=1.0) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def test_pipeline_policy_and_alert_endpoints(tmp_path: Path) -> None:
    """정책 조회/갱신 및 알람 조회 엔드포인트를 검증한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    preferred_port = _pick_free_port()
    config = AppConfig(
        db_path=db_path,
        host="127.0.0.1",
        preferred_port=preferred_port,
        max_port_scan=20,
        stop_grace_sec=10,
    )
    daemon_service = DaemonService(config=config, runtime_repo=RuntimeRepository(db_path))
    workspace_service = WorkspaceService(repository=WorkspaceRepository(db_path))
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    workspace_service.add_workspace(str(repo_dir))

    runtime = daemon_service.start()
    try:
        _wait_http_ready(f"http://{runtime.host}:{runtime.port}/health")
        policy = _read_json(f"http://{runtime.host}:{runtime.port}/pipeline/policy")
        assert "policy" in policy
        assert isinstance(policy["policy"], dict)

        updated = _read_json(
            (
                f"http://{runtime.host}:{runtime.port}/pipeline/policy?"
                "deletion_hold=on&workers=6&watcher_queue_max=12000&watcher_overflow_rescan_cooldown_sec=45"
            ),
            method="POST",
        )
        assert "policy" in updated
        assert updated["policy"]["deletion_hold"] is True
        assert updated["policy"]["enrich_worker_count"] == 6
        assert updated["policy"]["watcher_queue_max"] == 12000
        assert updated["policy"]["watcher_overflow_rescan_cooldown_sec"] == 45

        alert = _read_json(f"http://{runtime.host}:{runtime.port}/pipeline/alert")
        assert "alert" in alert
        assert isinstance(alert["alert"], dict)
        assert "state" in alert["alert"]

        auto_status = _read_json(f"http://{runtime.host}:{runtime.port}/pipeline/auto/status")
        assert "auto_control" in auto_status
        assert isinstance(auto_status["auto_control"], dict)

        auto_set = _read_json(f"http://{runtime.host}:{runtime.port}/pipeline/auto/set?enabled=on", method="POST")
        assert auto_set["auto_control"]["auto_hold_enabled"] is True

        auto_tick = _read_json(f"http://{runtime.host}:{runtime.port}/pipeline/auto/tick", method="POST")
        assert "action" in auto_tick
    finally:
        daemon_service.stop()
