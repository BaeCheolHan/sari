from __future__ import annotations

from sari.mcp.server_session_state import ensure_initialized_session


def test_ensure_initialized_session_acquires_when_needed():
    seen = {}

    class _Registry:
        def get_or_create(self, ws):
            seen["workspace"] = ws
            return {"session": True}

    session, acquired = ensure_initialized_session(
        session=None,
        injected_db=None,
        registry=_Registry(),
        workspace_root="/tmp/ws",
        session_acquired=False,
    )
    assert session == {"session": True}
    assert acquired is True
    assert seen["workspace"] == "/tmp/ws"


def test_ensure_initialized_session_keeps_existing_or_injected_mode():
    class _Registry:
        def get_or_create(self, _ws):
            raise AssertionError("should not be called")

    existing = {"session": "exists"}
    session, acquired = ensure_initialized_session(
        session=existing,
        injected_db=None,
        registry=_Registry(),
        workspace_root="/tmp/ws",
        session_acquired=False,
    )
    assert session is existing
    assert acquired is False

    session2, acquired2 = ensure_initialized_session(
        session=None,
        injected_db=object(),
        registry=_Registry(),
        workspace_root="/tmp/ws",
        session_acquired=False,
    )
    assert session2 is None
    assert acquired2 is False
