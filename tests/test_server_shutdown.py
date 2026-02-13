from __future__ import annotations

import threading

from sari.mcp.server_shutdown import perform_shutdown


def test_perform_shutdown_is_noop_when_already_stopped():
    stop = threading.Event()
    stop.set()
    called = {"n": 0}

    changed, _acquired, _session = perform_shutdown(
        stop_event=stop,
        executor=object(),
        transport=object(),
        logger=object(),
        close_all_daemon_connections=lambda: called.__setitem__("n", called["n"] + 1),
        registry=object(),
        workspace_root="/tmp/ws",
        session_acquired=True,
        session=object(),
        trace_fn=lambda *_a, **_k: None,
        log_debug=lambda _m: None,
    )
    assert changed is False
    assert called["n"] == 0


def test_perform_shutdown_runs_cleanup_and_releases_session():
    stop = threading.Event()
    calls: list[str] = []

    class _Executor:
        def shutdown(self, wait=True, cancel_futures=False):
            calls.append(f"executor:{wait}:{cancel_futures}")

    class _Transport:
        def close(self):
            calls.append("transport.close")

    class _Logger:
        def stop(self):
            calls.append("logger.stop")

    class _Registry:
        def release(self, ws):
            calls.append(f"release:{ws}")

    changed, acquired, session = perform_shutdown(
        stop_event=stop,
        executor=_Executor(),
        transport=_Transport(),
        logger=_Logger(),
        close_all_daemon_connections=lambda: calls.append("daemon.close_all"),
        registry=_Registry(),
        workspace_root="/tmp/ws",
        session_acquired=True,
        session=object(),
        trace_fn=lambda event, **_k: calls.append(event),
        log_debug=lambda _m: None,
    )
    assert changed is True
    assert acquired is False
    assert session is None
    assert "executor:True:False" in calls
    assert "transport.close" in calls
    assert "logger.stop" in calls
    assert "daemon.close_all" in calls
    assert "release:/tmp/ws" in calls


def test_perform_shutdown_swallows_cleanup_errors():
    stop = threading.Event()
    existing_session = object()

    class _BoomExecutor:
        def shutdown(self, wait=True, cancel_futures=False):
            raise RuntimeError("boom")

    changed, acquired, session = perform_shutdown(
        stop_event=stop,
        executor=_BoomExecutor(),
        transport=type("T", (), {"close": lambda self: (_ for _ in ()).throw(RuntimeError("x"))})(),
        logger=type("L", (), {"stop": lambda self: (_ for _ in ()).throw(RuntimeError("x"))})(),
        close_all_daemon_connections=lambda: (_ for _ in ()).throw(RuntimeError("x")),
        registry=type("R", (), {"release": lambda self, _ws: (_ for _ in ()).throw(RuntimeError("x"))})(),
        workspace_root="/tmp/ws",
        session_acquired=True,
        session=existing_session,
        trace_fn=lambda *_a, **_k: None,
        log_debug=lambda _m: None,
    )
    assert changed is True
    assert acquired is True
    assert session is existing_session
