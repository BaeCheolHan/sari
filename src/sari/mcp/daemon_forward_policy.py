"""MCP daemon forward 공통 정책을 제공한다."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import socket
import time
from typing import Callable

from sari.core.config import AppConfig
from sari.core.daemon_resolver import resolve_daemon_address
from sari.core.exceptions import DaemonError
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.server_daemon_forward import DaemonForwardError
from sari.services.daemon import DaemonService

StartDaemonFn = Callable[[Path, str | None], bool]
ResolveTargetFn = Callable[[Path, str | None, str | None, int | None], tuple[str, int]]
ForwardOnceFn = Callable[[dict[str, object], str, int, float], dict[str, object]]
ProbeOnceFn = Callable[[str, int, float], None]

FORWARD_METHODS = frozenset({"tools/list", "tools/call"})
LONG_RUNNING_TOOL_NAMES = frozenset({"scan_once", "rescan", "index_file"})
LONG_RUNNING_FORWARD_TIMEOUT_SEC = 60.0
POST_START_RETRY_MAX = 8
POST_START_RETRY_WAIT_SEC = 0.15
INITIALIZE_POST_START_RETRY_MAX = 40
INITIALIZE_POST_START_RETRY_WAIT_SEC = 0.2
DAEMON_START_READY_TIMEOUT_SEC = 20.0
DAEMON_START_READY_POLL_SEC = 0.2


def should_forward_to_daemon(proxy_enabled: bool, method: str) -> bool:
    """daemon forward 대상 메서드 여부를 반환한다."""
    if not proxy_enabled:
        return False
    return method in FORWARD_METHODS


def extract_workspace_root(payload: dict[str, object]) -> str | None:
    """요청 payload에서 repo(workspace_root)를 추출한다."""
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return None
    repo = arguments.get("repo")
    if not isinstance(repo, str):
        return None
    stripped = repo.strip()
    if stripped == "":
        return None
    return stripped


def resolve_target(
    db_path: Path,
    workspace_root: str | None,
    host_override: str | None,
    port_override: int | None,
) -> tuple[str, int]:
    """override 우선으로 daemon endpoint를 결정한다."""
    if host_override is not None and host_override.strip() != "" and port_override is not None:
        return host_override.strip(), port_override
    return resolve_daemon_address(db_path=db_path, workspace_root=workspace_root)


def forward_with_retry(
    request: dict[str, object],
    db_path: Path,
    workspace_root: str | None,
    host_override: str | None,
    port_override: int | None,
    timeout_sec: float,
    auto_start_on_failure: bool,
    start_daemon_fn: StartDaemonFn,
    resolve_target_fn: ResolveTargetFn,
    forward_once_fn: ForwardOnceFn,
    probe_once_fn: ProbeOnceFn | None = None,
) -> dict[str, object]:
    """forward 실패 시 자동 기동 후 1회 재시도한다."""
    retry_max, retry_wait_sec = resolve_post_start_retry_policy(request)
    replay_timeout_sec = resolve_forward_timeout_sec(request, default_timeout_sec=timeout_sec)
    host, port = resolve_target_fn(db_path, workspace_root, host_override, port_override)
    initial_timeout_sec = timeout_sec
    preflight_probe = default_probe_once if probe_once_fn is None else probe_once_fn
    try:
        if replay_timeout_sec > timeout_sec:
            preflight_probe(host, port, timeout_sec)
            initial_timeout_sec = replay_timeout_sec
        return forward_once_fn(request, host, port, initial_timeout_sec)
    except (DaemonForwardError, OSError, TimeoutError) as first_exc:
        if not auto_start_on_failure:
            raise
        if not is_retryable_error(first_exc):
            raise
        started = start_daemon_fn(db_path, workspace_root)
        if not started:
            method_raw = request.get("method")
            method = str(method_raw).strip().lower() if isinstance(method_raw, str) else ""
            if method == "initialize":
                raise TimeoutError("ERR_DAEMON_HANDSHAKE_TIMEOUT")
            raise TimeoutError("ERR_DAEMON_FORWARD_RETRY_EXHAUSTED")
        last_exc: BaseException | None = None
        for _ in range(retry_max):
            host_retry, port_retry = resolve_target_fn(db_path, workspace_root, host_override, port_override)
            try:
                return forward_once_fn(request, host_retry, port_retry, replay_timeout_sec)
            except (DaemonForwardError, OSError, TimeoutError) as retry_exc:
                last_exc = retry_exc
                if not is_retryable_error(retry_exc):
                    raise
                time.sleep(retry_wait_sec)
        if last_exc is not None:
            method_raw = request.get("method")
            method = str(method_raw).strip().lower() if isinstance(method_raw, str) else ""
            if method == "initialize":
                raise TimeoutError("ERR_DAEMON_HANDSHAKE_TIMEOUT")
            raise TimeoutError("ERR_DAEMON_FORWARD_RETRY_EXHAUSTED")
        raise


def resolve_post_start_retry_policy(request: dict[str, object]) -> tuple[int, float]:
    """요청 메서드에 따라 post-start 재시도 정책을 반환한다."""
    method_raw = request.get("method")
    method = str(method_raw).strip().lower() if isinstance(method_raw, str) else ""
    if method == "initialize":
        return INITIALIZE_POST_START_RETRY_MAX, INITIALIZE_POST_START_RETRY_WAIT_SEC
    return POST_START_RETRY_MAX, POST_START_RETRY_WAIT_SEC


def resolve_forward_timeout_sec(request: dict[str, object], default_timeout_sec: float) -> float:
    """요청 특성에 따라 daemon forward timeout을 조정한다."""
    method_raw = request.get("method")
    method = str(method_raw).strip().lower() if isinstance(method_raw, str) else ""
    if method != "tools/call":
        return default_timeout_sec
    params = request.get("params")
    if not isinstance(params, dict):
        return default_timeout_sec
    tool_name_raw = params.get("name")
    tool_name = str(tool_name_raw).strip().lower() if isinstance(tool_name_raw, str) else ""
    if tool_name in LONG_RUNNING_TOOL_NAMES:
        return max(default_timeout_sec, LONG_RUNNING_FORWARD_TIMEOUT_SEC)
    return default_timeout_sec


def resolve_tool_call_forward_timeout_sec(
    *,
    tool_name: str | None,
    default_timeout_sec: float,
) -> float:
    """tool name만 있는 경로(proxy replay 등)의 timeout을 계산한다."""
    normalized = str(tool_name or "").strip().lower()
    if normalized in LONG_RUNNING_TOOL_NAMES:
        return max(default_timeout_sec, LONG_RUNNING_FORWARD_TIMEOUT_SEC)
    return default_timeout_sec


def is_retryable_error(exc: BaseException) -> bool:
    """재시도 가능한 예외 타입인지 판정한다."""
    return isinstance(exc, (OSError, TimeoutError, DaemonForwardError))


def default_probe_once(host: str, port: int, timeout_sec: float) -> None:
    """daemon endpoint에 짧은 TCP 생존 probe를 수행한다."""
    with socket.create_connection((host, port), timeout=timeout_sec):
        pass


def build_forward_error_message(exc: BaseException) -> str:
    """forward 오류를 명시적 코드 접두사로 표준화한다."""
    raw_message = str(exc)
    if raw_message.startswith("ERR_"):
        return raw_message
    if isinstance(exc, TimeoutError):
        detail_code = "ERR_DAEMON_TIMEOUT"
    elif isinstance(exc, OSError):
        detail_code = "ERR_DAEMON_UNAVAILABLE"
    elif isinstance(exc, DaemonForwardError):
        detail_code = "ERR_DAEMON_PROTOCOL"
    else:
        detail_code = "ERR_DAEMON_FORWARD_UNKNOWN"
    return f"ERR_DAEMON_FORWARD_FAILED: {detail_code}: {exc}"


def default_start_daemon(db_path: Path, workspace_root: str | None) -> bool:
    """기본 daemon 자동 기동/attach 전략을 수행한다."""
    _ = workspace_root
    config = replace(AppConfig.default(), db_path=db_path)
    runtime_repo = RuntimeRepository(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    registry_repo = DaemonRegistryRepository(db_path)
    service = DaemonService(
        config=config,
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        registry_repo=registry_repo,
    )
    try:
        runtime = service.ensure_running(run_mode=config.run_mode)
        return wait_for_daemon_ready(
            host=runtime.host,
            port=runtime.port,
            timeout_sec=DAEMON_START_READY_TIMEOUT_SEC,
            poll_sec=DAEMON_START_READY_POLL_SEC,
        )
    except DaemonError:
        return False


def wait_for_daemon_ready(host: str, port: int, timeout_sec: float, poll_sec: float) -> bool:
    """daemon TCP endpoint가 연결 가능한 상태가 될 때까지 대기한다."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(poll_sec)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(poll_sec)
    return False
