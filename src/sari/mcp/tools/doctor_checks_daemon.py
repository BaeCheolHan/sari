"""Daemon/runtime related doctor checks."""

from __future__ import annotations

import os
from typing import Callable, TypeAlias

from sari.mcp.tools.doctor_common import compact_error_message

DoctorResult: TypeAlias = dict[str, object]


def check_daemon(
    *,
    result_fn: Callable[..., DoctorResult],
    get_daemon_address: Callable[[], tuple[str, int]],
    identify_daemon: Callable[[str, int], object],
    read_pid: Callable[[str, int], object],
    process_resources: Callable[[int], object],
    local_version: str,
    server_registry_cls: object,
) -> DoctorResult:
    host, port = get_daemon_address()
    identify = identify_daemon(host, port)
    running = identify is not None

    details = {}
    if running:
        pid = read_pid(host, port)
        remote_version = identify.get("version", "unknown")
        draining = identify.get("draining", False)

        if pid:
            details = process_resources(int(pid))

        status_msg = f"Running on {host}:{port} (PID: {pid}, v{remote_version})"
        if draining:
            status_msg += " [DRAINING]"
        if details:
            status_msg += f" [Mem: {details.get('mem_mb')}MB, CPU: {details.get('cpu_pct')}%]"

        if remote_version != local_version:
            return result_fn(
                "Sari Daemon",
                False,
                f"Version mismatch: local=v{local_version}, remote=v{remote_version}. {status_msg}",
            )
        return result_fn("Sari Daemon", True, status_msg)

    try:
        reg = server_registry_cls()
        data = reg.get_registry_snapshot(include_dead=True)
        for info in (data.get("daemons") or {}).values():
            if str(info.get("host") or "") != str(host):
                continue
            if int(info.get("port") or 0) != int(port):
                continue
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
                return result_fn(
                    "Sari Daemon",
                    False,
                    f"Not responding on {host}:{port} but PID {pid} is alive. Possible zombie or port conflict.",
                )
            except OSError:
                return result_fn(
                    "Sari Daemon",
                    False,
                    f"Not running, but stale registry entry exists (PID: {pid}).",
                )
    except Exception:
        pass

    return result_fn("Sari Daemon", False, "Not running")


def check_daemon_policy(
    *,
    result_fn: Callable[..., DoctorResult],
    load_policy: Callable[..., object],
    settings_obj: object,
    get_daemon_address: Callable[[], tuple[str, int]],
    resolve_http_endpoint_for_daemon: Callable[[str, int], tuple[str, int]],
    runtime_host_env: str,
    runtime_port_env: str,
) -> DoctorResult:
    policy = load_policy(settings_obj=settings_obj)
    daemon_host, daemon_port = get_daemon_address()
    http_host, http_port = resolve_http_endpoint_for_daemon(daemon_host, daemon_port)
    override_keys = (
        "SARI_DAEMON_HEARTBEAT_SEC",
        "SARI_DAEMON_IDLE_SEC",
        "SARI_DAEMON_IDLE_WITH_ACTIVE",
        "SARI_DAEMON_DRAIN_GRACE_SEC",
        "SARI_DAEMON_AUTOSTOP",
        "SARI_DAEMON_AUTOSTOP_GRACE_SEC",
        "SARI_DAEMON_SHUTDOWN_INHIBIT_MAX_SEC",
        "SARI_DAEMON_LEASE_TTL_SEC",
        runtime_host_env,
        runtime_port_env,
        "SARI_HTTP_HOST",
        "SARI_HTTP_PORT",
    )
    overrides = [k for k in override_keys if os.environ.get(k) not in (None, "")]
    detail = (
        f"daemon={daemon_host}:{daemon_port} http={http_host}:{http_port} "
        f"heartbeat_sec={policy.heartbeat_sec} idle_sec={policy.idle_sec} "
        f"idle_with_active={str(policy.idle_with_active).lower()} "
        f"autostop_enabled={str(policy.autostop_enabled).lower()} "
        f"autostop_grace_sec={policy.autostop_grace_sec} "
        f"shutdown_inhibit_max_sec={policy.shutdown_inhibit_max_sec} "
        f"lease_ttl_sec={policy.lease_ttl_sec} "
        f"overrides={','.join(overrides) if overrides else 'none'}"
    )
    return result_fn("Daemon Policy", True, detail)


def check_http_service(
    host: str,
    port: int,
    *,
    result_fn: Callable[..., DoctorResult],
    is_http_running: Callable[[str, int], bool],
) -> DoctorResult:
    running = is_http_running(host, port)
    if running:
        return result_fn("HTTP API", True, f"Running on {host}:{port}")
    return result_fn("HTTP API", False, f"Not running on {host}:{port}")


def check_daemon_runtime_markers(
    *,
    result_fn: Callable[..., DoctorResult],
    load_runtime_status: Callable[[], object],
) -> DoctorResult:
    try:
        status = load_runtime_status()
        detail = (
            f"shutdown_intent={str(bool(status.shutdown_intent)).lower()} "
            f"suicide_state={status.suicide_state} "
            f"active_leases={int(status.active_leases_count)} "
            f"event_queue_depth={int(status.event_queue_depth)} "
            f"workers_alive={len(list(status.workers_alive or []))}"
        )
        return result_fn("Daemon Runtime Markers", True, detail)
    except Exception as e:
        return result_fn("Daemon Runtime Markers", False, compact_error_message(e, "runtime marker load failed"))
