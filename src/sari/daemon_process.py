"""백그라운드 데몬 프로세스를 실행한다."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import threading
import time
from dataclasses import replace

import uvicorn

from sari.core.event_bus import EventBus
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.http.app import HttpContext, create_app
from sari.core.config import AppConfig
from sari.core.composition import build_file_collection_service_from_config, build_lsp_hub, build_repository_bundle, build_search_stack
from sari.core.exceptions import DaemonError, ErrorContext, PerfError, ValidationError
from sari.core.models import now_iso8601_utc
from sari.services.admin import AdminService
from sari.services.collection.service import SolidLspExtractionBackend
from sari.services.pipeline.control_service import PipelineControlService
from sari.services.pipeline.perf_service import PipelinePerfService
from sari.services.language_probe.service import LanguageProbeService
from sari.services.pipeline.lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline.quality_service import PipelineQualityService, SerenaGoldenBackend
from sari.services.read.facade_service import ReadFacadeService
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.server import McpServer
from sari.lsp.hub import LspHub as LspHub  # test monkeypatch compatibility

log = logging.getLogger(__name__)
_FATAL_DB_PATTERNS: tuple[str, ...] = (
    "disk i/o error",
    "no such table",
    "database disk image is malformed",
    "database or disk is full",
    "readonly database",
    "unable to open database file",
)


def parse_args() -> argparse.Namespace:
    """데몬 실행 인자를 파싱한다."""
    parser = argparse.ArgumentParser(prog="sari.daemon_process")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--run-mode", required=False, choices=["dev", "prod"], default="dev")
    return parser.parse_args()


def _build_daemon_config(*, db_path, host: str, port: int, run_mode: str) -> AppConfig:
    """파일/환경설정 로딩 결과에 CLI 런타임 인자를 오버레이한다."""
    loaded = AppConfig.default()
    return replace(
        loaded,
        db_path=db_path,
        host=host,
        preferred_port=port,
        max_port_scan=50,
        stop_grace_sec=10,
        run_mode=str(run_mode),
    )


def main() -> None:
    """데몬 HTTP 서버를 실행한다."""
    args = parse_args()
    from pathlib import Path

    db_path = Path(args.db_path)
    repos = build_repository_bundle(db_path)
    runtime_repo = repos.runtime_repo
    daemon_registry_repo = repos.daemon_registry_repo
    workspace_repo = repos.workspace_repo
    symbol_cache_repo = repos.symbol_cache_repo
    file_repo = repos.file_repo
    enrich_queue_repo = repos.enrich_queue_repo
    body_repo = repos.body_repo
    lsp_repo = repos.lsp_repo
    tool_layer_repo = repos.tool_layer_repo
    knowledge_repo = repos.knowledge_repo
    readiness_repo = repos.readiness_repo
    policy_repo = repos.policy_repo
    control_state_repo = repos.control_state_repo
    event_repo = repos.event_repo
    error_event_repo = repos.error_event_repo
    perf_repo = repos.perf_repo
    stage_baseline_repo = repos.stage_baseline_repo
    quality_repo = repos.quality_repo
    language_probe_repo = repos.language_probe_repo
    lsp_matrix_repo = repos.lsp_matrix_repo
    repo_registry_repo = repos.repo_registry_repo
    config = _build_daemon_config(
        db_path=db_path,
        host=args.host,
        port=args.port,
        run_mode=str(args.run_mode),
    )
    lsp_hub_config = config.lsp_hub_config()
    search_config = config.search_config()
    collection_config = config.collection_config()
    this_pid = os.getpid()
    launch_parent_pid = os.getppid()

    lsp_hub = build_lsp_hub(lsp_hub_config, hub_cls=LspHub)
    search_stack = build_search_stack(
        search_config=search_config,
        repos=repos,
        lsp_hub=lsp_hub,
        candidate_backend=search_config.candidate_backend,
        candidate_fallback_scan=search_config.candidate_fallback_scan,
        candidate_allowed_suffixes=collection_config.include_ext,
        blend_config_version="v2-config",
    )
    candidate_service = search_stack.candidate_service
    vector_sink = search_stack.vector_sink
    search_orchestrator = search_stack.orchestrator
    admin_service = AdminService(
        config=config,
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=symbol_cache_repo,
        queue_repo=enrich_queue_repo,
        registry_repo=daemon_registry_repo,
        lsp_reconciler=lsp_hub.reconcile_runtime,
    )
    detached_mode = os.getenv("SARI_DAEMON_DETACHED", "").strip().lower() in {"1", "true", "yes", "on"}

    event_bus = EventBus()

    file_collection_service = build_file_collection_service_from_config(
        config=config,
        repos=repos,
        event_bus=event_bus,
        lsp_backend=SolidLspExtractionBackend(
            lsp_hub,
            probe_workers=config.lsp_probe_workers,
            l1_workers=config.lsp_probe_l1_workers,
            force_join_ms=config.lsp_probe_force_join_ms,
            warming_retry_sec=config.lsp_probe_warming_retry_sec,
            warming_threshold=config.lsp_probe_warming_threshold,
            permanent_backoff_sec=config.lsp_probe_permanent_backoff_sec,
            symbol_normalizer_executor_mode=config.l5_symbol_normalizer_executor_mode,
            symbol_normalizer_subinterp_workers=config.l5_symbol_normalizer_subinterp_workers,
            symbol_normalizer_subinterp_min_symbols=config.l5_symbol_normalizer_subinterp_min_symbols,
            repo_language_probe_repo=repos.repo_language_probe_repo,
        ),
        run_mode=config.run_mode,
        parent_alive_probe=(lambda: _is_parent_alive(launch_parent_pid, detached_mode=detached_mode)),
        candidate_index_sink=candidate_service,
        vector_index_sink=vector_sink,
    )

    runtime_search_defaults: dict[str, bool] = {"resolve_symbols_default": False}

    def _set_search_resolve_symbols_default(enabled: bool) -> None:
        runtime_search_defaults["resolve_symbols_default"] = bool(enabled)

    def _get_search_resolve_symbols_default() -> bool:
        return bool(runtime_search_defaults.get("resolve_symbols_default", False))

    pipeline_control_service = PipelineControlService(
        policy_repo=policy_repo,
        event_repo=event_repo,
        queue_repo=enrich_queue_repo,
        control_state_repo=control_state_repo,
        set_l5_admission_mode=file_collection_service.set_l5_admission_mode,
        set_search_resolve_symbols_default=_set_search_resolve_symbols_default,
    )
    pipeline_quality_service = PipelineQualityService(
        file_repo=file_repo,
        lsp_repo=lsp_repo,
        quality_repo=quality_repo,
        golden_backend=SerenaGoldenBackend(hub=lsp_hub, lsp_repo=lsp_repo),
        artifact_root=config.db_path.parent / "artifacts",
        tool_layer_repo=tool_layer_repo,
    )
    pipeline_perf_service = PipelinePerfService(
        file_collection_service=file_collection_service,
        queue_repo=enrich_queue_repo,
        perf_repo=perf_repo,
        artifact_root=config.db_path.parent / "artifacts",
        stage_baseline_repo=stage_baseline_repo,
    )
    language_probe_service = LanguageProbeService(
        workspace_repo=workspace_repo,
        lsp_hub=lsp_hub,
        probe_repo=language_probe_repo,
        per_language_timeout_sec=config.lsp_probe_timeout_default_sec,
        per_language_timeout_overrides={"go": config.lsp_probe_timeout_go_sec},
        lsp_request_timeout_sec=config.lsp_request_timeout_sec,
        go_warmup_timeout_sec=config.lsp_probe_timeout_go_sec,
    )
    pipeline_lsp_matrix_service = PipelineLspMatrixService(
        probe_service=language_probe_service,
        run_repo=lsp_matrix_repo,
    )
    stabilization_service = StabilizationService()
    read_facade_service = ReadFacadeService(
        workspace_repo=workspace_repo,
        file_collection_service=file_collection_service,
        lsp_repo=lsp_repo,
        knowledge_repo=knowledge_repo,
        tool_layer_repo=tool_layer_repo,
        stabilization_service=stabilization_service,
    )

    os.environ["SARI_MCP_FORWARD_TO_DAEMON"] = "0"
    app = create_app(
        HttpContext(
            runtime_repo=runtime_repo,
            workspace_repo=workspace_repo,
            search_orchestrator=search_orchestrator,
            admin_service=admin_service,
            file_collection_service=file_collection_service,
            pipeline_control_service=pipeline_control_service,
            pipeline_perf_service=pipeline_perf_service,
            pipeline_quality_service=pipeline_quality_service,
            pipeline_lsp_matrix_service=pipeline_lsp_matrix_service,
            read_facade_service=read_facade_service,
            language_probe_repo=language_probe_repo,
            repo_registry_repo=repo_registry_repo,
            lsp_metrics_provider=lsp_hub.get_metrics,
            search_resolve_symbols_default_provider=_get_search_resolve_symbols_default,
            db_path=config.db_path,
            http_bg_proxy_enabled=False,
            http_bg_proxy_target="",
        )
    )
    app.state.mcp_server = McpServer(db_path=db_path)
    stop_event = threading.Event()
    shutdown_reason: dict[str, str] = {"value": "NORMAL_SHUTDOWN"}
    orphan_miss_count = 0
    reconcile_state = {
        "last_run_monotonic": time.monotonic(),
        "inflight": False,
    }
    reconcile_state_lock = threading.Lock()

    def _handle_sigterm(signum: int, frame: object) -> None:
        """SIGTERM 수신 시 종료 사유를 기록하고 종료 루프로 진입한다."""
        del signum, frame
        if shutdown_reason["value"] == "":
            shutdown_reason["value"] = "NORMAL_SHUTDOWN"
        stop_event.set()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    def _heartbeat_loop() -> None:
        """데몬 heartbeat를 주기적으로 갱신한다."""
        while not stop_event.is_set():
            try:
                touch_with_lease = getattr(runtime_repo, "touch_heartbeat_and_extend_lease", None)
                if callable(touch_with_lease):
                    touch_with_lease(
                        pid=this_pid,
                        heartbeat_at=now_iso8601_utc(),
                        lease_ttl_sec=int(config.daemon_stale_timeout_sec),
                    )
                else:
                    runtime_repo.touch_heartbeat(pid=this_pid, heartbeat_at=now_iso8601_utc())
                _touch_registry_seen(daemon_registry_repo, this_pid)
            except sqlite3.Error as exc:
                log.exception("heartbeat 갱신 실패: %s", exc)
                if _is_fatal_db_error(exc):
                    _trigger_fatal_shutdown(
                        stop_event=stop_event,
                        runtime_repo=runtime_repo,
                        pid=this_pid,
                        reason="DB_FATAL_HEARTBEAT",
                        shutdown_reason=shutdown_reason,
                    )
                    return
            stop_event.wait(timeout=float(config.daemon_heartbeat_interval_sec))

    def _auto_loop() -> None:
        """알람 기반 자동제어를 주기적으로 평가한다."""
        nonlocal orphan_miss_count
        last_auto_run_monotonic = 0.0
        while not stop_event.is_set():
            auto_interval = max(1.0, float(config.pipeline_auto_tick_interval_sec))
            orphan_interval = max(1.0, float(config.orphan_ppid_check_interval_sec))
            tick_wait = min(auto_interval, orphan_interval)
            try:
                should_terminate, orphan_miss_count = _should_orphan_terminate(
                    parent_alive=_is_parent_alive(launch_parent_pid, detached_mode=detached_mode),
                    detached_mode=detached_mode,
                    miss_count=orphan_miss_count,
                    confirm_probes=int(config.orphan_ppid_confirm_probes),
                )
                if should_terminate:
                    shutdown_reason["value"] = "ORPHAN_SELF_TERMINATE"
                    runtime_repo.mark_exit_reason(this_pid, "ORPHAN_SELF_TERMINATE", now_iso8601_utc())
                    stop_event.set()
                    os.kill(this_pid, signal.SIGTERM)
                    return
                now_monotonic = time.monotonic()
                if not _should_run_auto_control(
                    now_monotonic=now_monotonic,
                    last_run_monotonic=last_auto_run_monotonic,
                    interval_sec=auto_interval,
                ):
                    stop_event.wait(timeout=tick_wait)
                    continue
                pipeline_control_service.evaluate_auto_hold()
                try:
                    latest_perf_summary = pipeline_perf_service.get_latest_report()
                except PerfError:
                    latest_perf_summary = None
                rollout_action = pipeline_control_service.evaluate_stage_rollout(summary=latest_perf_summary)
                if bool(rollout_action.get("changed")):
                    event_repo.record_event(
                        job_id="daemon:stage_rollout",
                        status=str(rollout_action.get("action", "ROLLOUT_APPLIED")),
                        latency_ms=0,
                        created_at=now_iso8601_utc(),
                    )
                last_auto_run_monotonic = now_monotonic
            except (ValidationError, sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
                # 자동제어 실패를 침묵 처리하지 않고 명시적으로 기록한다.
                log.exception("자동제어 평가 실패: %s", exc)
                if isinstance(exc, sqlite3.Error) and _is_fatal_db_error(exc):
                    _trigger_fatal_shutdown(
                        stop_event=stop_event,
                        runtime_repo=runtime_repo,
                        pid=this_pid,
                        reason="DB_FATAL_AUTO_LOOP",
                        shutdown_reason=shutdown_reason,
                    )
                    return
                try:
                    event_repo.record_event(
                        job_id="daemon:auto_hold",
                        status="AUTO_LOOP_ERROR",
                        latency_ms=0,
                        created_at=now_iso8601_utc(),
                    )
                except sqlite3.Error as event_exc:
                    if not _handle_auto_loop_event_record_failure(
                        event_exc=event_exc,
                        stop_event=stop_event,
                        runtime_repo=runtime_repo,
                        pid=this_pid,
                        shutdown_reason=shutdown_reason,
                        tick_wait=tick_wait,
                    ):
                        return
                    continue
                if config.run_mode == "dev":
                    shutdown_reason["value"] = "AUTO_LOOP_FAILURE"
                    runtime_repo.mark_exit_reason(this_pid, "AUTO_LOOP_FAILURE", now_iso8601_utc())
                    stop_event.set()
                    os.kill(this_pid, signal.SIGTERM)
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_DAEMON_AUTO_LOOP_FAILED",
                            message=f"자동제어 평가 실패: {exc}",
                        )
                    ) from exc
                last_auto_run_monotonic = time.monotonic()
            stop_event.wait(timeout=tick_wait)

    def _reconcile_loop() -> None:
        """런타임 reconcile을 주기적으로 수행한다(auto loop와 분리)."""
        interval_sec = max(1.0, float(config.daemon_reconcile_interval_sec))
        while not stop_event.is_set():
            now_monotonic = time.monotonic()
            with reconcile_state_lock:
                should_run = _should_run_periodic_reconcile(
                    now_monotonic=now_monotonic,
                    last_run_monotonic=float(reconcile_state["last_run_monotonic"]),
                    interval_sec=interval_sec,
                    inflight=bool(reconcile_state["inflight"]),
                )
                if should_run:
                    reconcile_state["inflight"] = True
            if should_run:
                try:
                    _ = admin_service.runtime_reconcile()
                except (DaemonError, ValidationError, sqlite3.Error, RuntimeError, OSError, ValueError, TypeError) as exc:
                    log.exception("주기 reconcile 실패: %s", exc)
                    try:
                        event_repo.record_event(
                            job_id="daemon:runtime_reconcile",
                            status="RUNTIME_RECONCILE_ERROR",
                            latency_ms=0,
                            created_at=now_iso8601_utc(),
                        )
                    except sqlite3.Error:
                        log.exception("reconcile 실패 이벤트 기록 실패")
                finally:
                    with reconcile_state_lock:
                        reconcile_state["last_run_monotonic"] = time.monotonic()
                        reconcile_state["inflight"] = False
            stop_event.wait(timeout=min(interval_sec, float(config.orphan_ppid_check_interval_sec)))

    file_collection_service.start_background()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    auto_thread = threading.Thread(target=_auto_loop, daemon=True)
    reconcile_thread = threading.Thread(target=_reconcile_loop, daemon=True)
    heartbeat_thread.start()
    auto_thread.start()
    reconcile_thread.start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="error")
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        auto_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        reconcile_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        lsp_stop_error: DaemonError | None = None
        try:
            lsp_hub.stop_all()
        except DaemonError as exc:
            shutdown_reason["value"] = "LSP_STOP_FAILURE"
            runtime_repo.mark_exit_reason(this_pid, "LSP_STOP_FAILURE", now_iso8601_utc())
            lsp_stop_error = exc
        try:
            app.state.mcp_server.close()
        except DaemonError as exc:
            if lsp_stop_error is None:
                shutdown_reason["value"] = "MCP_CLOSE_FAILURE"
                runtime_repo.mark_exit_reason(this_pid, "MCP_CLOSE_FAILURE", now_iso8601_utc())
                lsp_stop_error = exc
        event_bus.shutdown()
        file_collection_service.stop_background()
        daemon_registry_repo.remove_by_pid(this_pid)
        runtime_repo.mark_exit_reason(this_pid, shutdown_reason["value"], now_iso8601_utc())
        if lsp_stop_error is not None:
            raise lsp_stop_error


def _is_parent_alive(parent_pid: int | None=None, detached_mode: bool=False) -> bool:
    """부모 프로세스 생존 여부를 확인한다."""
    resolved_parent_pid = os.getppid() if parent_pid is None else parent_pid
    if parent_pid is None and resolved_parent_pid <= 1:
        # 테스트/호환 경로에서는 ppid=1을 detached 상태로 간주한다.
        return True
    if detached_mode:
        # 백그라운드 분리 실행 데몬은 부모 종료를 정상 상태로 간주한다.
        return True
    if resolved_parent_pid <= 1:
        # 부모가 init(1)으로 변경되면 고아 상태로 간주해 즉시 종료 경로를 탄다.
        return False
    try:
        os.kill(resolved_parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _should_orphan_terminate(
    *,
    parent_alive: bool,
    detached_mode: bool,
    miss_count: int,
    confirm_probes: int,
) -> tuple[bool, int]:
    """orphan 종료 여부와 다음 miss 카운트를 계산한다."""
    if detached_mode:
        return (False, 0)
    if parent_alive:
        return (False, 0)
    next_count = max(0, int(miss_count)) + 1
    return (next_count >= max(1, int(confirm_probes)), next_count)


def _should_run_periodic_reconcile(
    *,
    now_monotonic: float,
    last_run_monotonic: float,
    interval_sec: float,
    inflight: bool,
) -> bool:
    """주기 reconcile 실행 여부를 결정한다."""
    if inflight:
        return False
    if now_monotonic < 0.0:
        return False
    due_after = max(0.0, float(last_run_monotonic)) + max(1.0, float(interval_sec))
    return float(now_monotonic) >= due_after


def _should_run_auto_control(
    *,
    now_monotonic: float,
    last_run_monotonic: float,
    interval_sec: float,
) -> bool:
    """자동제어 루프 실행 여부를 결정한다."""
    if now_monotonic < 0.0:
        return False
    due_after = max(0.0, float(last_run_monotonic)) + max(1.0, float(interval_sec))
    return float(now_monotonic) >= due_after


def _touch_registry_seen(registry_repo: DaemonRegistryRepository, pid: int) -> None:
    """현재 PID에 해당하는 registry 엔트리의 last_seen을 갱신한다."""
    for entry in registry_repo.list_all():
        if entry.pid == pid:
            registry_repo.touch(daemon_id=entry.daemon_id, seen_at=now_iso8601_utc())
            return


def _is_fatal_db_error(exc: sqlite3.Error) -> bool:
    """운영 지속이 불가능한 DB 오류인지 판정한다."""
    message = str(exc).strip().lower()
    return any(pattern in message for pattern in _FATAL_DB_PATTERNS)


def _handle_auto_loop_event_record_failure(
    *,
    event_exc: sqlite3.Error,
    stop_event: threading.Event,
    runtime_repo: RuntimeRepository,
    pid: int,
    shutdown_reason: dict[str, str],
    tick_wait: float,
) -> bool:
    """auto-loop 오류 이벤트 기록 실패를 처리한다.

    비치명 DB 오류는 loop 생존성을 위해 continue하고, 치명 오류만 즉시 종료한다.
    """
    if _is_fatal_db_error(event_exc):
        _trigger_fatal_shutdown(
            stop_event=stop_event,
            runtime_repo=runtime_repo,
            pid=pid,
            reason="DB_FATAL_EVENT_RECORD",
            shutdown_reason=shutdown_reason,
        )
        return False
    log.exception("자동제어 실패 이벤트 저장 실패(비치명): %s", event_exc)
    stop_event.wait(timeout=tick_wait)
    return True


def _trigger_fatal_shutdown(
    *,
    stop_event: threading.Event,
    runtime_repo: RuntimeRepository,
    pid: int,
    reason: str,
    shutdown_reason: dict[str, str],
) -> None:
    """DB 치명 오류 시 즉시 종료 경로로 전환한다."""
    shutdown_reason["value"] = reason
    try:
        runtime_repo.mark_exit_reason(pid, reason, now_iso8601_utc())
    except sqlite3.Error as mark_exc:
        log.exception("exit reason 기록 실패(%s): %s", reason, mark_exc)
    stop_event.set()
    os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
