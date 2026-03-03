"""MCP daemon forward 정책 유틸을 검증한다."""

from __future__ import annotations

from sari.mcp.daemon_forward_policy import (
    build_forward_error_message,
    forward_with_retry,
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
        )
        raise AssertionError("timeout error expected")
    except TimeoutError as exc:
        assert str(exc) == "ERR_DAEMON_FORWARD_RETRY_EXHAUSTED"
        assert build_forward_error_message(exc).startswith("ERR_DAEMON_FORWARD_RETRY_EXHAUSTED")
