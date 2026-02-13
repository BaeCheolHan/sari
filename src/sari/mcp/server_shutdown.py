"""Shutdown helpers for MCP server."""

from __future__ import annotations


def perform_shutdown(
    *,
    stop_event,
    executor: object,
    transport: object,
    logger: object,
    close_all_daemon_connections,
    registry: object,
    workspace_root: str,
    session_acquired: bool,
    session: object,
    trace_fn,
    log_debug,
) -> tuple[bool, bool, object]:
    if stop_event.is_set():
        return False, session_acquired, session
    stop_event.set()
    trace_fn("server_shutdown_start")

    try:
        executor.shutdown(wait=True, cancel_futures=False)
    except Exception as e:
        log_debug(f"Executor shutdown error: {e}")

    try:
        if transport and hasattr(transport, "close"):
            transport.close()
    except Exception:
        pass
    try:
        if logger and hasattr(logger, "stop"):
            logger.stop()
    except Exception:
        pass
    try:
        close_all_daemon_connections()
    except Exception:
        pass
    try:
        if session_acquired:
            registry.release(workspace_root)
            session_acquired = False
            session = None
    except Exception:
        pass

    trace_fn("server_shutdown_done")
    return True, session_acquired, session
