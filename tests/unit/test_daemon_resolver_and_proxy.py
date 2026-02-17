"""daemon resolver 및 proxy 설정 경로를 검증한다."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from pytest import MonkeyPatch, raises

from sari.cli.main import cli
from sari.core.daemon_resolver import resolve_daemon_address
from sari.core.models import DaemonRegistryEntryDTO, DaemonRuntimeDTO
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema
from sari.http.app import _parse_proxy_target


def test_daemon_resolver_prefers_registry_entry(tmp_path: Path) -> None:
    """resolver는 registry 엔트리를 runtime보다 우선해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    registry_repo = DaemonRegistryRepository(db_path)
    registry_repo.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-100",
            host="127.0.0.1",
            port=48888,
            pid=101,
            workspace_root="/repo/a",
            protocol="http",
            started_at="2026-02-16T11:00:00+00:00",
            last_seen_at="2026-02-16T11:00:01+00:00",
            is_draining=False,
        )
    )
    runtime_repo = RuntimeRepository(db_path)
    runtime_repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=202,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at="2026-02-16T11:00:00+00:00",
            session_count=0,
            last_heartbeat_at="2026-02-16T11:00:01+00:00",
            last_exit_reason=None,
        )
    )

    host, port = resolve_daemon_address(db_path=db_path, workspace_root="/repo/a")
    assert host == "127.0.0.1"
    assert port == 48888


def test_proxy_target_parse_validation() -> None:
    """proxy target 파서는 형식 오류를 명시적으로 거부해야 한다."""
    assert _parse_proxy_target("127.0.0.1:47777") == ("127.0.0.1", 47777)
    with raises(ValueError):
        _parse_proxy_target("127.0.0.1")
    with raises(ValueError):
        _parse_proxy_target(":47777")
    with raises(ValueError):
        _parse_proxy_target("127.0.0.1:abc")


def test_cli_mcp_proxy_invokes_proxy_runner(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI mcp proxy 명령은 proxy runner를 호출해야 한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".local" / "share" / "sari-v2").mkdir(parents=True, exist_ok=True)
    called: dict[str, object] = {}

    def _fake_run_proxy(
        db_path: Path,
        workspace_root: str | None,
        host_override: str | None,
        port_override: int | None,
        timeout_sec: float,
    ) -> int:
        called["db_path"] = db_path
        called["workspace_root"] = workspace_root
        called["host_override"] = host_override
        called["port_override"] = port_override
        called["timeout_sec"] = timeout_sec
        return 0

    monkeypatch.setattr("sari.cli.main.run_stdio_proxy", _fake_run_proxy)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["mcp", "proxy", "--workspace-root", "/repo/a", "--host", "127.0.0.1", "--port", "47777", "--timeout-sec", "1.5"],
    )

    assert result.exit_code == 0
    assert called["workspace_root"] == "/repo/a"
    assert called["host_override"] == "127.0.0.1"
    assert called["port_override"] == 47777
    assert called["timeout_sec"] == 1.5
