"""데몬과 HTTP 통합 동작을 검증한다."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
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


def _read_json(url: str) -> dict[str, object]:
    """JSON 응답을 읽어 파싱한다."""
    with urllib.request.urlopen(url, timeout=1.0) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def _read_text(url: str) -> tuple[int, str]:
    """텍스트 응답을 상태코드와 함께 반환한다."""
    with urllib.request.urlopen(url, timeout=1.0) as response:
        data = response.read().decode("utf-8")
        return int(response.status), data


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    """JSON POST 요청 응답을 읽어 파싱한다."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=1.0) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def _read_json_error(url: str) -> tuple[int, dict[str, object]]:
    """오류 응답 본문을 읽어 상태코드와 함께 반환한다."""
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            data = response.read().decode("utf-8")
            return int(response.status), json.loads(data)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return int(exc.code), json.loads(body)


def _build_services(tmp_path: Path, preferred_port: int) -> tuple[DaemonService, WorkspaceService]:
    """통합 테스트용 서비스를 구성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    config = AppConfig(
        db_path=db_path,
        host="127.0.0.1",
        preferred_port=preferred_port,
        max_port_scan=20,
        stop_grace_sec=10,
    )
    runtime_repo = RuntimeRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    daemon_service = DaemonService(config=config, runtime_repo=runtime_repo)
    workspace_service = WorkspaceService(repository=workspace_repo)
    return daemon_service, workspace_service


def test_daemon_start_status_stop_and_http(tmp_path: Path) -> None:
    """데몬 시작/상태/종료와 HTTP 엔드포인트를 검증한다."""
    daemon_service, workspace_service = _build_services(tmp_path=tmp_path, preferred_port=_pick_free_port())

    runtime = daemon_service.start()
    try:
        _wait_http_ready(f"http://{runtime.host}:{runtime.port}/health")
        health = _read_json(f"http://{runtime.host}:{runtime.port}/health")
        assert health["status"] == "ok"
        assert "version" in health
        assert "uptime_sec" in health

        status_payload = _read_json(f"http://{runtime.host}:{runtime.port}/status")
        daemon_payload = status_payload["daemon"]
        assert isinstance(daemon_payload, dict)
        assert daemon_payload["pid"] == runtime.pid
        assert daemon_payload["host"] == runtime.host
        assert daemon_payload["port"] == runtime.port
        assert isinstance(daemon_payload["last_heartbeat_at"], str)
        assert "workspace_count" in status_payload
        assert status_payload["run_mode"] == "prod"
        assert "pipeline_metrics" in status_payload
        assert isinstance(status_payload["pipeline_metrics"], dict)
        assert "daemon_lifecycle" in status_payload
        lifecycle = status_payload["daemon_lifecycle"]
        assert isinstance(lifecycle, dict)
        assert isinstance(lifecycle["last_heartbeat_at"], str)
        assert isinstance(lifecycle["heartbeat_age_sec"], float)

        repo_dir = tmp_path / "repo-a"
        repo_dir.mkdir()
        source_file = repo_dir / "sample.py"
        source_file.write_text("def test_symbol():\n    return 'ok'\n", encoding="utf-8")
        workspace_service.add_workspace(str(repo_dir))

        workspaces = _read_json(f"http://{runtime.host}:{runtime.port}/workspaces")
        assert isinstance(workspaces["items"], list)
        assert len(workspaces["items"]) == 1

        query = urllib.parse.quote("test_symbol")
        repo_q = urllib.parse.quote(str(repo_dir))
        search = _read_json(f"http://{runtime.host}:{runtime.port}/search?repo={repo_q}&q={query}&limit=5")
        assert "items" in search
        assert "meta" in search
        assert isinstance(search["items"], list)
        assert len(search["items"]) >= 1
        assert "candidate_source" in search["meta"]

        read_json = _post_json(
            f"http://{runtime.host}:{runtime.port}/read_diff_preview",
            {
                "repo": str(repo_dir),
                "target": "sample.py",
                "content": "def test_symbol():\n    return 'changed'\n",
            },
        )
        assert "items" in read_json
        assert isinstance(read_json["items"], list)
        assert len(read_json["items"]) == 1
        assert read_json["items"][0]["path"] == "sample.py"

        read_pack1 = _post_json(
            f"http://{runtime.host}:{runtime.port}/read_diff_preview",
            {
                "repo": str(repo_dir),
                "target": "sample.py",
                "content": "def test_symbol():\n    return 'changed'\n",
                "format": "pack1",
            },
        )
        assert read_pack1["isError"] is False
        assert "structuredContent" in read_pack1

        code, bad_search = _read_json_error(f"http://{runtime.host}:{runtime.port}/search?repo={repo_q}&q={query}&limit=bad")
        assert code == 400
        assert "error" in bad_search
        assert bad_search["error"]["code"] == "ERR_INVALID_LIMIT"
        assert isinstance(bad_search["error"]["message"], str)

        error_api = _read_json(f"http://{runtime.host}:{runtime.port}/api/pipeline/errors?limit=5")
        assert "items" in error_api
        assert isinstance(error_api["items"], list)

        html_code, html_body = _read_text(f"http://{runtime.host}:{runtime.port}/pipeline/errors")
        assert html_code == 200
        assert "Pipeline Error Events" in html_body

        status = daemon_service.status()
        assert status is not None
        assert status.pid == runtime.pid
    finally:
        daemon_service.stop()

    assert daemon_service.status() is None


def test_daemon_reassigns_port_when_conflict(tmp_path: Path) -> None:
    """기본 포트 충돌 시 자동 재할당 동작을 검증한다."""
    preferred_port = _pick_free_port()
    daemon_service, _ = _build_services(tmp_path=tmp_path, preferred_port=preferred_port)

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", preferred_port))
    blocker.listen(1)

    runtime = daemon_service.start()
    try:
        assert runtime.port != preferred_port
    finally:
        daemon_service.stop()
        blocker.close()
