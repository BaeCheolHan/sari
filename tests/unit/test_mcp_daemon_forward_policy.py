"""MCP daemon forward 정책 유틸을 검증한다."""

from __future__ import annotations

from sari.mcp.daemon_forward_policy import (
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
