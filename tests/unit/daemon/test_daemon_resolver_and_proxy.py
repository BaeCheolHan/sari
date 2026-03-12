"""daemon resolver 및 proxy 설정 경로를 검증한다."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from click.testing import CliRunner
from pytest import MonkeyPatch, raises

from sari.cli.main import cli
from sari.core.daemon_resolver import resolve_daemon_address, resolve_daemon_endpoint
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


def test_daemon_resolver_exposes_resolution_reason(tmp_path: Path) -> None:
    """resolver는 선택 근거를 함께 반환해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    registry_repo = DaemonRegistryRepository(db_path)
    registry_repo.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-200",
            host="127.0.0.1",
            port=49999,
            pid=301,
            workspace_root="/repo/b",
            protocol="http",
            started_at="2026-02-18T00:00:00+00:00",
            last_seen_at="2026-02-18T00:00:01+00:00",
            is_draining=False,
        )
    )
    resolved = resolve_daemon_endpoint(db_path=db_path, workspace_root="/repo/b")
    assert resolved.host == "127.0.0.1"
    assert resolved.port == 49999
    assert resolved.reason == "registry_active"


def test_daemon_resolver_skips_degraded_registry_entry(tmp_path: Path) -> None:
    """degraded 엔트리는 registry 우선순위에서 제외하고 runtime fallback을 사용해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    registry_repo = DaemonRegistryRepository(db_path)
    registry_repo.upsert(
        DaemonRegistryEntryDTO(
            daemon_id="d-300",
            host="127.0.0.1",
            port=49998,
            pid=302,
            workspace_root="/repo/c",
            protocol="http",
            started_at="2026-02-18T00:00:00+00:00",
            last_seen_at="2026-02-18T00:00:01+00:00",
            is_draining=False,
            deployment_state="DEGRADED",
        )
    )
    runtime_repo = RuntimeRepository(db_path)
    runtime_repo.upsert_runtime(
        DaemonRuntimeDTO(
            pid=999,
            host="127.0.0.1",
            port=47777,
            state="running",
            started_at="2026-02-18T00:00:02+00:00",
            session_count=0,
            last_heartbeat_at="2026-02-18T00:00:03+00:00",
            last_exit_reason=None,
        )
    )
    resolved = resolve_daemon_endpoint(db_path=db_path, workspace_root="/repo/c")
    assert resolved.port == 47777
    assert resolved.reason == "runtime"


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


def test_cli_mcp_stdio_defaults_to_proxy(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """mcp stdio 기본 경로는 proxy runner를 호출해야 한다."""
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
        called["proxy"] = True
        called["db_path"] = db_path
        called["workspace_root"] = workspace_root
        called["host_override"] = host_override
        called["port_override"] = port_override
        called["timeout_sec"] = timeout_sec
        return 0

    def _fake_run_stdio(db_path: Path) -> int:
        called["local"] = True
        called["db_path_local"] = db_path
        return 0

    monkeypatch.setattr("sari.cli.main.run_stdio_proxy", _fake_run_proxy)
    monkeypatch.setattr("sari.cli.main.run_stdio", _fake_run_stdio)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["mcp", "stdio", "--workspace-root", "/repo/a", "--host", "127.0.0.1", "--port", "47777", "--timeout-sec", "1.5"],
    )

    assert result.exit_code == 0
    assert called.get("proxy") is True
    assert called.get("local") is None
    assert called["workspace_root"] == "/repo/a"
    assert called["host_override"] == "127.0.0.1"
    assert called["port_override"] == 47777
    assert called["timeout_sec"] == 1.5


def test_cli_mcp_stdio_local_flag_uses_local_server(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """mcp stdio --local 경로는 run_stdio를 호출해야 한다."""
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
        _ = (db_path, workspace_root, host_override, port_override, timeout_sec)
        called["proxy"] = True
        return 0

    def _fake_run_stdio(db_path: Path) -> int:
        called["local"] = True
        called["db_path_local"] = db_path
        return 0

    monkeypatch.setattr("sari.cli.main.run_stdio_proxy", _fake_run_proxy)
    monkeypatch.setattr("sari.cli.main.run_stdio", _fake_run_stdio)
    runner = CliRunner()
    result = runner.invoke(cli, ["mcp", "stdio", "--local"])

    assert result.exit_code == 0
    assert called.get("local") is True
    assert called.get("proxy") is None


class _ScriptedTransport:
    """proxy 루프 검증을 위한 스크립트형 transport 더블이다."""

    def __init__(self, messages: list[tuple[dict[str, object], str] | None]) -> None:
        """읽기 시퀀스와 쓰기 버퍼를 초기화한다."""
        self._messages = messages
        self._index = 0
        self.writes: list[tuple[dict[str, object], str | None]] = []
        self.default_mode = "content-length"

    def read_message(self) -> tuple[dict[str, object], str] | None:
        """다음 입력 메시지를 반환한다."""
        if self._index >= len(self._messages):
            return None
        item = self._messages[self._index]
        self._index += 1
        return item

    def write_message(self, message: dict[str, object], mode: str | None = None) -> None:
        """출력 메시지를 기록한다."""
        self.writes.append((message, mode))


class _DelayedEofTransport:
    """watchdog 개입 검증을 위해 EOF를 지연시키는 transport 더블이다."""

    def __init__(self, delay_sec: float = 0.2) -> None:
        self._delay_sec = delay_sec
        self._returned = False
        self.writes: list[tuple[dict[str, object], str | None]] = []
        self.default_mode = "content-length"

    def read_message(self) -> tuple[dict[str, object], str] | None:
        if self._returned:
            return None
        self._returned = True
        time.sleep(self._delay_sec)
        return None

    def write_message(self, message: dict[str, object], mode: str | None = None) -> None:
        self.writes.append((message, mode))


def test_proxy_auto_start_retries_forward_once(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """proxy는 첫 연결 실패 시 daemon 기동 후 1회 재시도해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(messages=[({"jsonrpc": "2.0", "id": 7, "method": "ping"}, "content-length"), None])
    called: dict[str, Any] = {"forward": 0, "start": 0}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forward"] += 1
        if called["forward"] == 1:
            raise OSError("connection refused")
        return {"jsonrpc": "2.0", "id": 7, "result": {}}

    def _fake_start_daemon(db_path: Path, workspace_root: str | None) -> bool:
        _ = (db_path, workspace_root)
        called["start"] += 1
        return True

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=True,
        start_daemon_fn=_fake_start_daemon,
    )

    assert exit_code == 0
    assert called["start"] == 1
    assert called["forward"] == 2
    assert len(transport.writes) == 1
    assert transport.writes[0][0]["result"] == {}


def test_proxy_exits_when_parent_is_gone(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """부모가 사라지면 stdio proxy는 orphan 종료를 요청하고 빠져나와야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _DelayedEofTransport(delay_sec=0.15)
    terminate_called: list[int] = []

    def _fake_transport(*_: object, **__: object) -> _DelayedEofTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
        parent_alive_fn=lambda _: False,
        input_hangup_fn=lambda: True,
        orphan_check_interval_sec=0.01,
        self_terminate_fn=lambda pid: terminate_called.append(pid),
    )

    assert exit_code == 0
    assert len(terminate_called) == 1
    assert transport.writes == []


def test_proxy_orphan_shutdown_does_not_emit_protocol_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """orphan 종료는 protocol error 응답 없이 조용히 종료되어야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _DelayedEofTransport(delay_sec=0.15)
    terminate_called: list[int] = []

    def _fake_transport(*_: object, **__: object) -> _DelayedEofTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
        parent_alive_fn=lambda _: False,
        input_hangup_fn=lambda: True,
        orphan_check_interval_sec=0.01,
        self_terminate_fn=lambda pid: terminate_called.append(pid),
    )

    assert exit_code == 0
    assert len(terminate_called) == 1
    assert transport.writes == []


def test_proxy_watchdog_thread_stops_on_eof(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """입력 EOF 종료에서는 orphan terminate 요청 없이 정상 종료해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(messages=[None])
    terminate_called: list[int] = []

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
        parent_alive_fn=lambda _: True,
        orphan_check_interval_sec=0.01,
        self_terminate_fn=lambda pid: terminate_called.append(pid),
    )

    assert exit_code == 0
    assert terminate_called == []


def test_proxy_parent_gone_without_input_hangup_does_not_self_terminate(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """부모 종료만으로는 자가종료하지 않고 입력 EOF를 기다려 정상 종료해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _DelayedEofTransport(delay_sec=0.05)
    terminate_called: list[int] = []

    def _fake_transport(*_: object, **__: object) -> _DelayedEofTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
        parent_alive_fn=lambda _: False,
        input_hangup_fn=lambda: False,
        orphan_check_interval_sec=0.01,
        self_terminate_fn=lambda pid: terminate_called.append(pid),
    )

    assert exit_code == 0
    assert terminate_called == []


def test_proxy_keyboard_interrupt_returns_130(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """SIGINT/KeyboardInterrupt 경로는 성공(0)으로 덮지 않고 130을 반환해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    class _InterruptTransport:
        default_mode = "content-length"

        def read_message(self) -> tuple[dict[str, object], str] | None:
            raise KeyboardInterrupt

        def write_message(self, message: dict[str, object], mode: str | None = None) -> None:
            _ = (message, mode)

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", lambda *_, **__: _InterruptTransport())

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 130


def test_proxy_auto_start_waits_until_daemon_ready(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """proxy는 auto-start 후 연결거부가 반복되더라도 준비 완료까지 재시도해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(messages=[({"jsonrpc": "2.0", "id": 17, "method": "ping"}, "content-length"), None])
    called: dict[str, Any] = {"forward": 0, "start": 0}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forward"] += 1
        if called["forward"] <= 3:
            raise OSError("connection refused")
        return {"jsonrpc": "2.0", "id": 17, "result": {"ok": True}}

    def _fake_start_daemon(db_path: Path, workspace_root: str | None) -> bool:
        _ = (db_path, workspace_root)
        called["start"] += 1
        return True

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=True,
        start_daemon_fn=_fake_start_daemon,
    )

    assert exit_code == 0
    assert called["start"] == 1
    assert called["forward"] == 4
    assert len(transport.writes) == 1
    assert transport.writes[0][0]["result"] == {"ok": True}


def test_proxy_initialize_waits_for_cold_start_daemon_ready(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """initialize 요청은 콜드 스타트 준비 완료까지 충분히 재시도해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[({"jsonrpc": "2.0", "id": 29, "method": "initialize", "params": {}}, "content-length"), None]
    )
    called: dict[str, Any] = {"forward": 0, "start": 0}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forward"] += 1
        if called["forward"] <= 10:
            raise OSError("connection refused")
        return {"jsonrpc": "2.0", "id": 29, "result": {"protocolVersion": "2025-06-18"}}

    def _fake_start_daemon(db_path: Path, workspace_root: str | None) -> bool:
        _ = (db_path, workspace_root)
        called["start"] += 1
        return True

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)
    monkeypatch.setattr("sari.mcp.daemon_forward_policy.time.sleep", lambda _: None)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=True,
        start_daemon_fn=_fake_start_daemon,
    )

    assert exit_code == 0
    assert called["start"] == 1
    assert called["forward"] == 11
    assert len(transport.writes) == 1
    assert transport.writes[0][0]["result"]["protocolVersion"] == "2025-06-18"


def test_proxy_forward_failure_returns_explicit_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """proxy forward 실패는 명시적 오류코드 메시지로 응답해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(messages=[({"jsonrpc": "2.0", "id": 11, "method": "ping"}, "content-length"), None])

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr(
        "sari.mcp.proxy.forward_once",
        lambda request, host, port, timeout_sec: (_ for _ in ()).throw(TimeoutError("dial timeout")),
    )

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    assert payload["error"]["code"] == -32002
    assert str(payload["error"]["message"]).startswith("ERR_DAEMON_FORWARD_FAILED:")


def test_proxy_initializes_schema_before_resolve_target(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """빈 DB에서도 proxy가 스키마를 먼저 초기화하고 forward 오류로 응답해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    db_path = tmp_path / "state.db"
    transport = _ScriptedTransport(messages=[({"jsonrpc": "2.0", "id": 41, "method": "initialize", "params": {}}, "content-length"), None])

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr(
        "sari.mcp.proxy.forward_once",
        lambda request, host, port, timeout_sec: (_ for _ in ()).throw(OSError("connection refused")),
    )

    exit_code = run_stdio_proxy(
        db_path=db_path,
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    assert payload["error"]["code"] == -32002
    assert str(payload["error"]["message"]).startswith("ERR_DAEMON_FORWARD_FAILED:")

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daemon_runtime'").fetchone()
    assert row is not None


def test_proxy_reconnects_when_draining_response_received(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """draining 응답을 받으면 endpoint 재해석 후 initialize 재전송 + 요청 재시도를 수행해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[
            ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, "content-length"),
            (
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "scan_once",
                        "arguments": {"repo": "/repo/a"},
                    },
                },
                "content-length",
            ),
            None,
        ]
    )
    calls: list[object] = []
    timeouts: list[float] = []

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (host, port)
        calls.append(request.get("method"))
        timeouts.append(timeout_sec)
        method = str(request.get("method", ""))
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"protocolVersion": "2026-01-01"}}
        if calls.count("tools/call") == 1:
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32001, "message": "daemon draining"}}
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"content": [], "isError": False}}

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 2
    assert "error" not in transport.writes[1][0]
    assert calls == ["initialize", "tools/call", "initialize", "tools/call"]
    assert timeouts[0] == 2.0
    assert timeouts[1] > 2.0
    assert timeouts[2] == 2.0
    assert timeouts[3] > 2.0


def test_proxy_does_not_reply_to_notifications(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """notification 메시지(id 없음)에는 응답을 쓰지 않아야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[
            ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, "content-length"),
            ({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, "content-length"),
            ({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, "content-length"),
            None,
        ]
    )

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (host, port, timeout_sec)
        method = str(request.get("method", ""))
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": None, "result": {}}
        return {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 2
    assert transport.writes[0][0]["id"] == 1
    assert transport.writes[1][0]["id"] == 2


def test_proxy_tools_list_hides_internal_tool_group(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """tools/list 응답에서는 내부 관리 도구군만 숨기고 knowledge는 노출해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(messages=[({"jsonrpc": "2.0", "id": 31, "method": "tools/list"}, "content-length"), None])

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        return {
            "jsonrpc": "2.0",
            "id": 31,
            "result": {
                "tools": [
                    {"name": "search", "inputSchema": {"type": "object"}},
                    {"name": "knowledge", "inputSchema": {"type": "object"}},
                    {"name": "save_snippet", "inputSchema": {"type": "object"}},
                    {"name": "get_snippet", "inputSchema": {"type": "object"}},
                    {"name": "archive_context", "inputSchema": {"type": "object"}},
                    {"name": "get_context", "inputSchema": {"type": "object"}},
                    {"name": "pipeline_policy_get", "inputSchema": {"type": "object"}},
                    {"name": "pipeline_quality_run", "inputSchema": {"type": "object"}},
                ]
            },
        }

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    tools = payload["result"]["tools"]
    tool_names = {str(tool["name"]) for tool in tools}
    assert "search" in tool_names
    assert "knowledge" in tool_names
    assert "save_snippet" not in tool_names
    assert "get_snippet" not in tool_names
    assert "archive_context" not in tool_names
    assert "get_context" not in tool_names
    assert "pipeline_policy_get" not in tool_names
    assert "pipeline_quality_run" not in tool_names


def test_proxy_blocks_hidden_tools_call(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """숨김 도구 호출은 daemon으로 전달하지 않고 tool not found로 차단해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[
            (
                {
                    "jsonrpc": "2.0",
                    "id": 32,
                    "method": "tools/call",
                    "params": {"name": "save_snippet", "arguments": {"repo": "/repo/a"}},
                },
                "content-length",
            ),
            None,
        ]
    )
    called = {"forwarded": False}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forwarded"] = True
        return {"jsonrpc": "2.0", "id": 32, "result": {"ok": True}}

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert called["forwarded"] is False
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "tool not found"


def test_proxy_allows_knowledge_call(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """knowledge 호출은 숨김 차단 없이 daemon으로 전달되어야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[
            (
                {
                    "jsonrpc": "2.0",
                    "id": 34,
                    "method": "tools/call",
                    "params": {"name": "knowledge", "arguments": {"repo": "/repo/a", "query": "x"}},
                },
                "content-length",
            ),
            None,
        ]
    )
    called = {"forwarded": False}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (host, port, timeout_sec)
        called["forwarded"] = True
        assert str(request.get("method", "")) == "tools/call"
        params = request.get("params")
        assert isinstance(params, dict)
        assert params.get("name") == "knowledge"
        return {"jsonrpc": "2.0", "id": 34, "result": {"isError": False, "structuredContent": {"items": []}}}

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert called["forwarded"] is True
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    assert "error" not in payload


def test_proxy_blocks_hidden_pipeline_tool_call(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """pipeline 내부 관리 도구 호출도 daemon 전달 없이 차단해야 한다."""
    from sari.mcp.proxy import run_stdio_proxy

    transport = _ScriptedTransport(
        messages=[
            (
                {
                    "jsonrpc": "2.0",
                    "id": 33,
                    "method": "tools/call",
                    "params": {"name": "pipeline_quality_run", "arguments": {"repo": "/repo/a"}},
                },
                "content-length",
            ),
            None,
        ]
    )
    called = {"forwarded": False}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forwarded"] = True
        return {"jsonrpc": "2.0", "id": 33, "result": {"ok": True}}

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)

    exit_code = run_stdio_proxy(
        db_path=tmp_path / "state.db",
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert called["forwarded"] is False
    assert len(transport.writes) == 1
    payload = transport.writes[0][0]
    assert payload["error"]["code"] == -32601
    assert payload["error"]["message"] == "tool not found"


def test_proxy_degrades_on_repo_id_integrity_failure_without_forwarding(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """repo_id integrity 오류가 있어도 proxy는 degraded MCP 응답을 반환해야 한다."""
    from sari.db.schema import connect, init_schema
    from sari.mcp.proxy import run_stdio_proxy

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_index_changes(
                change_type, status, repo_id, repo_root, scope_repo_root, relative_path,
                absolute_path, content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
            ) VALUES(
                'UPSERT', 'PENDING', '', '/broken/repo', '/broken/repo', 'a.py',
                '/broken/repo/a.py', 'h1', 1, 10, 'test', NULL, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """
        )
        conn.commit()

    transport = _ScriptedTransport(
        messages=[
            ({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, "content-length"),
            ({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "content-length"),
            (
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {"repo": "sari", "query": "x", "structured": 1}},
                },
                "content-length",
            ),
            None,
        ]
    )
    called = {"forwarded": False, "started": False}

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    def _fake_forward_once(request: dict[str, object], host: str, port: int, timeout_sec: float) -> dict[str, object]:
        _ = (request, host, port, timeout_sec)
        called["forwarded"] = True
        return {"jsonrpc": "2.0", "id": 1, "result": {}}

    def _fake_start_daemon(db_path: Path, workspace_root: str | None) -> bool:
        _ = (db_path, workspace_root)
        called["started"] = True
        return True

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.forward_once", _fake_forward_once)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))

    exit_code = run_stdio_proxy(
        db_path=db_path,
        workspace_root="/repo/a",
        auto_start_on_failure=True,
        start_daemon_fn=_fake_start_daemon,
    )

    assert exit_code == 0
    assert called["forwarded"] is False
    assert called["started"] is False
    assert len(transport.writes) == 3
    init_payload = transport.writes[0][0]
    tools_payload = transport.writes[1][0]
    call_payload = transport.writes[2][0]
    assert init_payload["result"]["serverInfo"]["name"] == "sari-v2"
    tool_names = {str(tool["name"]) for tool in tools_payload["result"]["tools"]}
    assert tool_names == {"doctor", "repo_candidates", "sari_guide", "status"}
    assert call_payload["result"]["isError"] is True
    assert call_payload["result"]["structuredContent"]["error"]["code"] == "ERR_MCP_STARTUP_DEGRADED"


def test_proxy_degraded_path_skips_notification_replies(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """degraded startup 경로에서도 notification에는 응답하지 않아야 한다."""
    from sari.db.schema import connect, init_schema
    from sari.mcp.proxy import run_stdio_proxy

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_index_changes(
                change_type, status, repo_id, repo_root, scope_repo_root, relative_path,
                absolute_path, content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
            ) VALUES(
                'UPSERT', 'PENDING', '', '/broken/repo', '/broken/repo', 'a.py',
                '/broken/repo/a.py', 'h1', 1, 10, 'test', NULL, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """
        )
        conn.commit()

    transport = _ScriptedTransport(
        [
            ({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, "content-length"),
            ({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, "content-length"),
            ({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "content-length"),
            None,
        ]
    )

    def _fake_transport(*_: object, **__: object) -> _ScriptedTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)
    monkeypatch.setattr("sari.mcp.proxy.resolve_target", lambda *_: ("127.0.0.1", 47777))

    exit_code = run_stdio_proxy(
        db_path=db_path,
        workspace_root="/repo/a",
        auto_start_on_failure=False,
    )

    assert exit_code == 0
    assert len(transport.writes) == 2
    assert transport.writes[0][0]["id"] == 1
    assert transport.writes[1][0]["id"] == 2


def test_proxy_degraded_path_preserves_orphan_self_termination(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """degraded startup 경로도 orphan watchdog을 유지해야 한다."""
    from sari.db.schema import connect, init_schema
    from sari.mcp.proxy import run_stdio_proxy

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_index_changes(
                change_type, status, repo_id, repo_root, scope_repo_root, relative_path,
                absolute_path, content_hash, mtime_ns, size_bytes, event_source, reason, created_at, updated_at
            ) VALUES(
                'UPSERT', 'PENDING', '', '/broken/repo', '/broken/repo', 'a.py',
                '/broken/repo/a.py', 'h1', 1, 10, 'test', NULL, '2026-03-05T00:00:00Z', '2026-03-05T00:00:00Z'
            )
            """
        )
        conn.commit()

    transport = _DelayedEofTransport(delay_sec=0.15)
    terminate_called: list[int] = []

    def _fake_transport(*_: object, **__: object) -> _DelayedEofTransport:
        return transport

    monkeypatch.setattr("sari.mcp.proxy.McpTransport", _fake_transport)

    exit_code = run_stdio_proxy(
        db_path=db_path,
        workspace_root="/repo/a",
        auto_start_on_failure=False,
        parent_alive_fn=lambda _: False,
        input_hangup_fn=lambda: True,
        orphan_check_interval_sec=0.01,
        self_terminate_fn=lambda pid: terminate_called.append(pid),
    )

    assert exit_code == 0
    assert len(terminate_called) == 1
    assert transport.writes == []
