"""stdio <-> daemon MCP framed 프록시를 구현한다."""

from __future__ import annotations

import logging
import os
import select
import signal
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Callable

from sari.core.exceptions import SariBaseError
from sari.mcp.daemon_forward_policy import (
    StartDaemonFn,
    build_forward_error_message,
    default_start_daemon,
    forward_with_retry,
    resolve_target,
)
from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.server import DegradedMcpServer, resolve_stdio_startup_issue
from sari.mcp.server_daemon_forward import DaemonForwardError, forward_once
from sari.mcp.tool_visibility import filter_tools_list_response_payload, is_hidden_tool_name
from sari.mcp.transport import MCP_MODE_FRAMED, McpTransport, McpTransportParseError

log = logging.getLogger(__name__)
ParentAliveFn = Callable[[int], bool]
SelfTerminateFn = Callable[[int], None]
InputHangupFn = Callable[[], bool]


def _env_bool(name: str, default: bool) -> bool:
    """불리언 환경변수를 파싱한다."""
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float) -> float:
    """실수 환경변수를 파싱하고 하한값을 적용한다."""
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    return max(minimum, value)


def _is_parent_alive(parent_pid: int) -> bool:
    """parent pid 생존 여부를 확인한다."""
    if parent_pid <= 1:
        return False
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _request_self_terminate(pid: int) -> None:
    """현재 프로세스에 SIGTERM을 전달해 blocking read를 깨운다."""
    os.kill(pid, signal.SIGTERM)


def _is_input_hung_up(input_stream: object) -> bool:
    """입력 스트림에 hangup(POLLHUP/POLLERR)이 감지됐는지 확인한다."""
    fileno_fn = getattr(input_stream, "fileno", None)
    if not callable(fileno_fn):
        return False
    try:
        fd = int(fileno_fn())
    except (OSError, ValueError, TypeError):
        return False
    if fd < 0:
        return True
    if not hasattr(select, "poll"):
        # poll 미지원 환경에서는 보수적으로 hangup 미감지로 처리한다.
        return False
    poller = select.poll()
    try:
        poller.register(fd, select.POLLHUP | select.POLLERR | select.POLLNVAL)
        events = poller.poll(0)
    except (OSError, ValueError):
        return False
    if len(events) == 0:
        return False
    flags = int(events[0][1])
    return bool(flags & (select.POLLHUP | select.POLLERR | select.POLLNVAL))


def _is_initialize_request(payload: dict[str, object]) -> bool:
    """payload가 initialize 요청인지 반환한다."""
    return str(payload.get("method", "")).strip() == "initialize"


def _is_tools_list_request(payload: dict[str, object]) -> bool:
    """payload가 tools/list 요청인지 반환한다."""
    return str(payload.get("method", "")).strip() == "tools/list"


def _extract_tools_call_name(payload: dict[str, object]) -> str | None:
    """tools/call 요청에서 도구명을 추출한다."""
    if str(payload.get("method", "")).strip() != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    if normalized == "":
        return None
    return normalized


def _is_draining_response(payload: dict[str, object]) -> bool:
    """daemon 응답이 draining 오류인지 판정한다."""
    error_obj = payload.get("error")
    if not isinstance(error_obj, dict):
        return False
    code_raw = error_obj.get("code")
    message_raw = str(error_obj.get("message", "")).lower()
    if isinstance(code_raw, int) and code_raw == -32001 and "draining" in message_raw:
        return True
    return "draining" in message_raw


def _is_draining_reconnect_enabled() -> bool:
    """draining 자동 재연결 활성화 여부를 반환한다."""
    raw = os.getenv("SARI_MCP_DRAINING_RECONNECT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _retry_after_draining(
    *,
    payload: dict[str, object],
    db_path: Path,
    workspace_root: str | None,
    host_override: str | None,
    port_override: int | None,
    timeout_sec: float,
    initialize_payload: dict[str, object] | None,
) -> dict[str, object]:
    """draining 응답 이후 endpoint 재해석 + initialize 재전송 + 원요청 재시도한다."""
    host_retry, port_retry = resolve_target(db_path, workspace_root, host_override, port_override)
    if initialize_payload is not None and not _is_initialize_request(payload):
        initialize_response = forward_once(initialize_payload, host_retry, port_retry, timeout_sec)
        if _is_draining_response(initialize_response):
            raise ValueError("ERR_DAEMON_DRAINING_RECONNECT_FAILED: initialize replay still draining")
    retried = forward_once(payload, host_retry, port_retry, timeout_sec)
    if _is_draining_response(retried):
        raise ValueError("ERR_DAEMON_DRAINING_RECONNECT_FAILED: request replay still draining")
    return retried


def run_stdio_proxy(
    db_path: Path,
    workspace_root: str | None = None,
    host_override: str | None = None,
    port_override: int | None = None,
    timeout_sec: float = 2.0,
    auto_start_on_failure: bool = True,
    start_daemon_fn: StartDaemonFn | None = None,
    parent_alive_fn: ParentAliveFn | None = None,
    orphan_check_interval_sec: float | None = None,
    self_terminate_fn: SelfTerminateFn | None = None,
    input_hangup_fn: InputHangupFn | None = None,
) -> int:
    """표준 입출력 MCP 요청을 daemon endpoint로 중계한다."""
    input_stream = getattr(sys.stdin, "buffer", sys.stdin)
    output_stream = getattr(sys.stdout, "buffer", sys.stdout)
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream, allow_jsonl=True)
    transport.default_mode = MCP_MODE_FRAMED
    try:
        startup_issue = resolve_stdio_startup_issue(db_path)
    except SariBaseError as exc:
        print(f"sari mcp stdio startup failed: {exc.context.code}: {exc.context.message}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"sari mcp stdio startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    degraded_server: DegradedMcpServer | None = None
    if startup_issue is not None:
        log.warning("mcp proxy startup degraded(code=%s message=%s)", startup_issue.code, startup_issue.message)
        degraded_server = DegradedMcpServer(db_path=db_path, startup_error=startup_issue)
    daemon_starter = default_start_daemon if start_daemon_fn is None else start_daemon_fn
    parent_alive_checker = _is_parent_alive if parent_alive_fn is None else parent_alive_fn
    self_terminator = _request_self_terminate if self_terminate_fn is None else self_terminate_fn
    input_hangup_checker = (lambda: _is_input_hung_up(input_stream)) if input_hangup_fn is None else input_hangup_fn
    watchdog_enabled = _env_bool("SARI_MCP_STDIO_ORPHAN_EXIT_ENABLED", True)
    check_interval_sec = (
        _env_float("SARI_MCP_STDIO_ORPHAN_CHECK_INTERVAL_SEC", 1.0, minimum=0.01)
        if orphan_check_interval_sec is None
        else max(0.01, float(orphan_check_interval_sec))
    )
    this_pid = os.getpid()
    launch_parent_pid = os.getppid()
    stop_event = threading.Event()
    initialize_payload: dict[str, object] | None = None
    watchdog_thread: threading.Thread | None = None
    previous_sigterm: object | None = None
    previous_sigint: object | None = None

    def _handle_termination(signum: int, frame: object) -> None:
        """종료 신호 수신 시 루프를 종료한다."""
        del frame
        stop_event.set()
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(0)

    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, _handle_termination)
        signal.signal(signal.SIGINT, _handle_termination)

    def _orphan_watchdog() -> None:
        """부모 소멸 시 stdio proxy를 자가 종료한다."""
        if not watchdog_enabled:
            return
        while not stop_event.wait(timeout=check_interval_sec):
            if parent_alive_checker(launch_parent_pid):
                continue
            # 부모 PID만으로 종료하면 래퍼 프로세스 환경에서 false positive가 발생할 수 있으므로,
            # stdio 입력 hangup이 실제로 감지된 경우에만 orphan 종료를 수행한다.
            if not input_hangup_checker():
                continue
            log.warning(
                "stdio proxy orphan detected; requesting self termination "
                "(reason=ORPHAN_SELF_TERMINATE pid=%s parent_pid=%s current_ppid=%s)",
                this_pid,
                launch_parent_pid,
                os.getppid(),
            )
            stop_event.set()
            try:
                self_terminator(this_pid)
            except (ProcessLookupError, PermissionError):
                return
            except OSError:
                log.exception("failed to self terminate orphan stdio proxy")
            return

    watchdog_thread = threading.Thread(target=_orphan_watchdog, daemon=True)
    watchdog_thread.start()

    try:
        while not stop_event.is_set():
            try:
                read_result = transport.read_message()
            except McpTransportParseError as exc:
                if stop_event.is_set():
                    return 0
                parse_response = McpResponse(request_id=None, result=None, error=McpError(code=-32700, message=str(exc)))
                transport.write_message(parse_response.to_dict(), mode=exc.mode)
                continue

            if read_result is None:
                return 0
            payload, mode = read_result
            request_id = payload.get("id")
            is_notification = request_id is None
            if degraded_server is not None:
                if is_notification:
                    continue
                response = degraded_server.handle_request(payload)
                transport.write_message(response.to_dict(), mode=mode)
                continue
            hidden_tool_name = _extract_tools_call_name(payload)
            if hidden_tool_name is not None and is_hidden_tool_name(hidden_tool_name):
                if is_notification:
                    continue
                response = McpResponse(
                    request_id=request_id,
                    result=None,
                    error=McpError(code=-32601, message="tool not found"),
                )
                transport.write_message(response.to_dict(), mode=mode)
                continue
            if _is_initialize_request(payload):
                initialize_payload = payload
            try:
                forwarded = forward_with_retry(
                    request=payload,
                    db_path=db_path,
                    workspace_root=workspace_root,
                    host_override=host_override,
                    port_override=port_override,
                    timeout_sec=timeout_sec,
                    auto_start_on_failure=auto_start_on_failure,
                    start_daemon_fn=daemon_starter,
                    resolve_target_fn=resolve_target,
                    forward_once_fn=forward_once,
                )
                if _is_draining_reconnect_enabled() and _is_draining_response(forwarded):
                    forwarded = _retry_after_draining(
                        payload=payload,
                        db_path=db_path,
                        workspace_root=workspace_root,
                        host_override=host_override,
                        port_override=port_override,
                        timeout_sec=timeout_sec,
                        initialize_payload=initialize_payload,
                    )
                if _is_tools_list_request(payload):
                    forwarded = filter_tools_list_response_payload(forwarded)
                if is_notification:
                    continue
                transport.write_message(forwarded, mode=mode)
            except (DaemonForwardError, OSError, TimeoutError, ValueError) as exc:
                if is_notification:
                    continue
                message = build_forward_error_message(exc)
                if "database is locked" in str(exc).lower():
                    message = (
                        f"{message} (hint: another 'sari mcp stdio' process may be "
                        "holding the state DB lock)"
                    )
                response = McpResponse(
                    request_id=request_id,
                    result=None,
                    error=McpError(code=-32002, message=message),
                )
                transport.write_message(response.to_dict(), mode=mode)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        if code is None:
            return 0
        return 0
    finally:
        stop_event.set()
        if watchdog_thread is not None:
            watchdog_thread.join(timeout=max(0.2, check_interval_sec * 2.0))
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)
        if degraded_server is not None:
            degraded_server.close()
