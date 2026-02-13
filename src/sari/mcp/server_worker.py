"""Worker/queue helper functions for MCP server."""

from __future__ import annotations

import queue
from typing import Callable, Optional


JsonMap = dict[str, object]


def worker_loop(
    *,
    stop_event: object,
    req_queue: queue.Queue,
    submit_request_for_execution: Callable[[JsonMap], bool],
    log_debug: Callable[[str], None],
) -> None:
    while not stop_event.is_set():
        try:
            req = req_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        except Exception as e:
            log_debug(f"Queue access error in worker loop: {e}")
            continue

        should_break = False
        try:
            should_break = not submit_request_for_execution(req)
        finally:
            try:
                req_queue.task_done()
            except Exception:
                pass
        if should_break:
            break


def enqueue_incoming_request(
    *,
    req_queue: queue.Queue,
    req: JsonMap,
    emit_queue_overload: Callable[[JsonMap], None],
    log_debug: Callable[[str], None],
    trace_fn: Callable[..., None],
) -> None:
    try:
        req_queue.put(req, timeout=0.01)
    except queue.Full:
        emit_queue_overload(req)
    except Exception as e:
        log_debug(f"ERROR putting req to queue: {e}")
        trace_fn("run_loop_queue_error", error=str(e))


def emit_queue_overload(
    *,
    req: JsonMap,
    stdout_lock: object,
    transport: object,
    log_debug: Callable[[str], None],
    trace_fn: Callable[..., None],
) -> None:
    msg_id = req.get("id")
    if msg_id is not None:
        error_resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32003,
                "message": "Server overloaded: request queue is full. Please try again later.",
            },
        }
        mode = req.get("_sari_framing_mode", "content-length")
        with stdout_lock:
            if transport is not None:
                transport.write_message(error_resp, mode=mode)
    log_debug(f"CRITICAL: MCP request queue is full! Dropping request {msg_id}")
    trace_fn("run_loop_queue_full", msg_id=msg_id)


def submit_request_for_execution(
    *,
    executor: object,
    handle_and_respond: Callable[[JsonMap], None],
    req: JsonMap,
    log_debug: Callable[[str], None],
) -> bool:
    try:
        executor.submit(handle_and_respond, req)
        return True
    except RuntimeError as e:
        log_debug(f"Executor shutdown during submit: {e}")
        return False
    except Exception as e:
        log_debug(f"Error submitting to executor: {e}")
        return True


def drain_pending_requests(
    *,
    req_queue: queue.Queue,
    handle_and_respond: Callable[[JsonMap], None],
) -> None:
    while True:
        try:
            req = req_queue.get_nowait()
        except queue.Empty:
            break
        try:
            handle_and_respond(req)
        finally:
            try:
                req_queue.task_done()
            except Exception:
                pass


def handle_and_respond(
    *,
    req: JsonMap,
    handle_request: Callable[[JsonMap], Optional[JsonMap]],
    force_content_length: bool,
    log_debug_response: Callable[[str, JsonMap], None],
    transport: object,
    stdout_lock: object,
    log_debug: Callable[[str], None],
    trace_fn: Callable[..., None],
) -> None:
    try:
        trace_fn(
            "handle_and_respond_enter",
            msg_id=req.get("id"),
            method=req.get("method"),
        )
        resp = handle_request(req)
        if resp:
            req_mode = req.get("_sari_framing_mode", "content-length")
            if force_content_length and req_mode != "jsonl":
                mode = "content-length"
            else:
                mode = req_mode
            log_debug_response(mode, resp)
            if transport is None:
                raise RuntimeError("transport is not initialized")
            with stdout_lock:
                transport.write_message(resp, mode=mode)
            trace_fn("handle_and_respond_sent", msg_id=req.get("id"), mode=mode)
    except Exception as e:
        log_debug(f"ERROR in _handle_and_respond: {e}")
        trace_fn("handle_and_respond_error", msg_id=req.get("id"), error=str(e))
