"""MCP daemon forward 정책 유틸을 검증한다."""

from __future__ import annotations

from sari.mcp.daemon_forward_policy import (
    default_probe_once,
    build_forward_error_message,
    forward_with_retry,
    resolve_forward_timeout_sec,
    resolve_post_start_retry_policy,
    wait_for_daemon_ready,
)


def test_resolve_post_start_retry_policy_for_initialize() -> None:
    """initialize 요청은 확장된 재시도 정책을 사용해야 한다."""
    retry_max, retry_wait = resolve_post_start_retry_policy({"method": "initialize"})
    assert retry_max >= 40
    assert retry_wait >= 0.2


def test_wait_for_daemon_ready_returns_true_when_port_open(monkeypatch) -> None:
    """TCP 연결 성공 시 준비 완료를 반환해야 한다."""

    class _FakeSocket:
        def __enter__(self) -> "_FakeSocket":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

        def settimeout(self, timeout: float) -> None:
            _ = timeout

        def connect_ex(self, addr: tuple[str, int]) -> int:
            _ = addr
            return 0

    monkeypatch.setattr("sari.mcp.daemon_forward_policy.socket.socket", lambda *_: _FakeSocket())
    assert wait_for_daemon_ready(host="127.0.0.1", port=47777, timeout_sec=1.0, poll_sec=0.01) is True


def test_forward_with_retry_returns_handshake_timeout_for_initialize() -> None:
    """initialize 재시도 소진 시 handshake timeout 코드로 실패해야 한다."""

    def _forward_once(request: dict[str, object], host: str, port: int, timeout: float) -> dict[str, object]:
        del request, host, port, timeout
        raise TimeoutError("connect timeout")

    def _resolve(db_path, workspace_root, host_override, port_override):  # type: ignore[no-untyped-def]
        del db_path, workspace_root, host_override, port_override
        return ("127.0.0.1", 47777)

    try:
        forward_with_retry(
            request={"method": "initialize"},
            db_path=None,  # type: ignore[arg-type]
            workspace_root=None,
            host_override=None,
            port_override=None,
            timeout_sec=0.05,
            auto_start_on_failure=True,
            start_daemon_fn=lambda db_path, workspace_root: False,
            resolve_target_fn=_resolve,
            forward_once_fn=_forward_once,
            probe_once_fn=lambda host, port, timeout: None,
        )
        raise AssertionError("timeout error expected")
    except TimeoutError as exc:
        assert str(exc) == "ERR_DAEMON_HANDSHAKE_TIMEOUT"
        assert build_forward_error_message(exc).startswith("ERR_DAEMON_HANDSHAKE_TIMEOUT")


def test_forward_with_retry_returns_retry_exhausted_for_non_initialize() -> None:
    """일반 요청 재시도 소진 시 retry exhausted 코드로 실패해야 한다."""

    def _forward_once(request: dict[str, object], host: str, port: int, timeout: float) -> dict[str, object]:
        del request, host, port, timeout
        raise TimeoutError("connect timeout")

    def _resolve(db_path, workspace_root, host_override, port_override):  # type: ignore[no-untyped-def]
        del db_path, workspace_root, host_override, port_override
        return ("127.0.0.1", 47777)

    try:
        forward_with_retry(
            request={"method": "tools/call"},
            db_path=None,  # type: ignore[arg-type]
            workspace_root=None,
            host_override=None,
            port_override=None,
            timeout_sec=0.05,
            auto_start_on_failure=True,
            start_daemon_fn=lambda db_path, workspace_root: False,
            resolve_target_fn=_resolve,
            forward_once_fn=_forward_once,
            probe_once_fn=lambda host, port, timeout: None,
        )
        raise AssertionError("timeout error expected")
    except TimeoutError as exc:
        assert str(exc) == "ERR_DAEMON_FORWARD_RETRY_EXHAUSTED"
        assert build_forward_error_message(exc).startswith("ERR_DAEMON_FORWARD_RETRY_EXHAUSTED")


def test_resolve_forward_timeout_sec_extends_scan_once_tool_calls() -> None:
    """scan_once 같은 장시간 MCP 도구는 기본 forward timeout보다 긴 시간을 받아야 한다."""
    timeout = resolve_forward_timeout_sec(
        {
            "method": "tools/call",
            "params": {"name": "scan_once"},
        },
        default_timeout_sec=2.0,
    )
    assert timeout > 2.0


def test_resolve_forward_timeout_sec_extends_get_implementations_tool_calls() -> None:
    """구조적 Protocol 스캔이 필요한 get_implementations도 확장 timeout을 받아야 한다."""
    timeout = resolve_forward_timeout_sec(
        {
            "method": "tools/call",
            "params": {"name": "get_implementations"},
        },
        default_timeout_sec=2.0,
    )
    assert timeout > 2.0


def test_resolve_forward_timeout_sec_keeps_default_for_short_tools() -> None:
    """일반 짧은 도구는 기존 기본 timeout을 유지해야 한다."""
    timeout = resolve_forward_timeout_sec(
        {
            "method": "tools/call",
            "params": {"name": "search_symbol"},
        },
        default_timeout_sec=2.0,
    )
    assert timeout == 2.0


def test_forward_with_retry_keeps_initial_probe_short_and_request_long_for_long_running_tool() -> None:
    """장시간 도구는 짧은 preflight 뒤 실제 요청에만 확장 timeout을 사용해야 한다."""
    seen: list[float] = []
    probed: list[float] = []

    def _forward_once(request: dict[str, object], host: str, port: int, timeout: float) -> dict[str, object]:
        del request, host, port
        seen.append(timeout)
        if len(seen) == 1:
            raise TimeoutError("connect timeout")
        return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    def _probe_once(host: str, port: int, timeout: float) -> None:
        del host, port
        probed.append(timeout)

    def _resolve(db_path, workspace_root, host_override, port_override):  # type: ignore[no-untyped-def]
        del db_path, workspace_root, host_override, port_override
        return ("127.0.0.1", 47777)

    result = forward_with_retry(
        request={"method": "tools/call", "params": {"name": "scan_once"}},
        db_path=None,  # type: ignore[arg-type]
        workspace_root=None,
        host_override=None,
        port_override=None,
        timeout_sec=2.0,
        auto_start_on_failure=True,
        start_daemon_fn=lambda db_path, workspace_root: True,
        resolve_target_fn=_resolve,
        forward_once_fn=_forward_once,
        probe_once_fn=_probe_once,
    )

    assert result["result"]["ok"] is True
    assert probed == [2.0]
    assert seen[0] > 2.0
    assert seen[1] > 2.0


def test_default_probe_once_uses_dual_stack_create_connection(monkeypatch) -> None:
    """TCP preflight는 IPv4/IPv6를 모두 처리할 수 있어야 한다."""
    seen: list[tuple[object, float | None]] = []

    class _FakeSocket:
        def __enter__(self) -> "_FakeSocket":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    def _create_connection(addr, timeout=None):  # type: ignore[no-untyped-def]
        seen.append((addr, timeout))
        return _FakeSocket()

    monkeypatch.setattr("sari.mcp.daemon_forward_policy.socket.create_connection", _create_connection)
    default_probe_once("::1", 47777, 2.0)
    assert seen == [(("::1", 47777), 2.0)]
