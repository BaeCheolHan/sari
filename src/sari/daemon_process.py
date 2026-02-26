"""백그라운드 데몬 프로세스를 실행한다."""

import argparse
import logging
import os
import signal
import sqlite3
import threading

import uvicorn

from sari.http.app import HttpContext, create_app
from sari.core.config import AppConfig
from sari.core.composition import build_lsp_hub, build_repository_bundle, build_search_stack
from sari.core.exceptions import DaemonError, ErrorContext, PerfError, ValidationError
from sari.core.models import now_iso8601_utc
from sari.services.admin import AdminService
from sari.services.collection.service import SolidLspExtractionBackend, build_default_file_collection_service
from sari.services.pipeline.control_service import PipelineControlService
from sari.services.pipeline.perf_service import PipelinePerfService
from sari.services.language_probe.service import LanguageProbeService
from sari.services.pipeline.lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline.quality_service import PipelineQualityService, SerenaGoldenBackend
from sari.services.read.facade_service import ReadFacadeService
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.server import McpServer

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
    config = AppConfig(
        db_path=db_path,
        host=args.host,
        preferred_port=args.port,
        max_port_scan=50,
        stop_grace_sec=10,
        run_mode=str(args.run_mode),
    )
    this_pid = os.getpid()
    launch_parent_pid = os.getppid()

    lsp_hub = build_lsp_hub(config)
    search_stack = build_search_stack(
        config=config,
        repos=repos,
        lsp_hub=lsp_hub,
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

    file_collection_service = build_default_file_collection_service(
        workspace_repo=workspace_repo,
        file_repo=file_repo,
        enrich_queue_repo=enrich_queue_repo,
        body_repo=body_repo,
        lsp_repo=lsp_repo,
        readiness_repo=readiness_repo,
        policy_repo=policy_repo,
        event_repo=event_repo,
        error_event_repo=error_event_repo,
        candidate_index_sink=candidate_service,
        vector_index_sink=vector_sink,
        retry_max_attempts=config.pipeline_retry_max,
        retry_backoff_base_sec=config.pipeline_backoff_base_sec,
        queue_poll_interval_ms=config.queue_poll_interval_ms,
        include_ext=config.collection_include_ext,
        exclude_globs=config.collection_exclude_globs,
        watcher_debounce_ms=config.watcher_debounce_ms,
        run_mode=config.run_mode,
        parent_alive_probe=(lambda: _is_parent_alive(launch_parent_pid, detached_mode=detached_mode)),
        lsp_backend=SolidLspExtractionBackend(
            lsp_hub,
            probe_workers=config.lsp_probe_workers,
            l1_workers=config.lsp_probe_l1_workers,
            force_join_ms=config.lsp_probe_force_join_ms,
            warming_retry_sec=config.lsp_probe_warming_retry_sec,
            warming_threshold=config.lsp_probe_warming_threshold,
            permanent_backoff_sec=config.lsp_probe_permanent_backoff_sec,
        ),
        l3_parallel_enabled=config.l3_parallel_enabled,
        l3_executor_max_workers=config.l3_executor_max_workers,
        l3_recent_success_ttl_sec=config.l3_recent_success_ttl_sec,
        l3_backpressure_on_interactive=config.l3_backpressure_on_interactive,
        l3_backpressure_cooldown_ms=config.l3_backpressure_cooldown_ms,
        l3_supported_languages=config.l3_supported_languages,
        lsp_probe_bootstrap_file_window=config.lsp_probe_bootstrap_file_window,
        lsp_probe_bootstrap_top_k=config.lsp_probe_bootstrap_top_k,
        lsp_probe_language_priority=config.lsp_probe_language_priority,
        lsp_probe_l1_languages=config.lsp_probe_l1_languages,
        lsp_scope_planner_enabled=config.lsp_scope_planner_enabled,
        lsp_scope_planner_shadow_mode=config.lsp_scope_planner_shadow_mode,
        lsp_scope_java_markers=config.lsp_scope_java_markers,
        lsp_scope_ts_markers=config.lsp_scope_ts_markers,
        lsp_scope_vue_markers=config.lsp_scope_vue_markers,
        lsp_scope_top_level_fallback=config.lsp_scope_top_level_fallback,
        lsp_scope_active_languages=config.lsp_scope_active_languages,
        lsp_session_broker_enabled=config.lsp_session_broker_enabled,
        lsp_session_broker_metrics_enabled=config.lsp_session_broker_metrics_enabled,
        lsp_broker_optional_scaffolding_enabled=config.lsp_broker_optional_scaffolding_enabled,
        lsp_broker_batch_throughput_mode_enabled=config.lsp_broker_batch_throughput_mode_enabled,
        lsp_broker_batch_throughput_pending_threshold=config.lsp_broker_batch_throughput_pending_threshold,
        lsp_broker_batch_disable_java_probe=config.lsp_broker_batch_disable_java_probe,
        lsp_hotness_event_window_sec=config.lsp_hotness_event_window_sec,
        lsp_hotness_decay_window_sec=config.lsp_hotness_decay_window_sec,
        lsp_broker_backlog_min_share=config.lsp_broker_backlog_min_share,
        lsp_broker_max_standby_sessions_per_lang=config.lsp_broker_max_standby_sessions_per_lang,
        lsp_broker_max_standby_sessions_per_budget_group=config.lsp_broker_max_standby_sessions_per_budget_group,
        lsp_broker_ts_vue_active_cap=config.lsp_broker_ts_vue_active_cap,
        lsp_broker_java_hot_lanes=config.lsp_broker_java_hot_lanes,
        lsp_broker_java_backlog_lanes=config.lsp_broker_java_backlog_lanes,
        lsp_broker_java_sticky_ttl_sec=config.lsp_broker_java_sticky_ttl_sec,
        lsp_broker_java_switch_cooldown_sec=config.lsp_broker_java_switch_cooldown_sec,
        lsp_broker_java_min_lease_ms=config.lsp_broker_java_min_lease_ms,
        lsp_broker_ts_hot_lanes=config.lsp_broker_ts_hot_lanes,
        lsp_broker_ts_backlog_lanes=config.lsp_broker_ts_backlog_lanes,
        lsp_broker_ts_sticky_ttl_sec=config.lsp_broker_ts_sticky_ttl_sec,
        lsp_broker_ts_switch_cooldown_sec=config.lsp_broker_ts_switch_cooldown_sec,
        lsp_broker_ts_min_lease_ms=config.lsp_broker_ts_min_lease_ms,
        lsp_broker_vue_hot_lanes=config.lsp_broker_vue_hot_lanes,
        lsp_broker_vue_backlog_lanes=config.lsp_broker_vue_backlog_lanes,
        lsp_broker_vue_sticky_ttl_sec=config.lsp_broker_vue_sticky_ttl_sec,
        lsp_broker_vue_switch_cooldown_sec=config.lsp_broker_vue_switch_cooldown_sec,
        lsp_broker_vue_min_lease_ms=config.lsp_broker_vue_min_lease_ms,
        l5_call_rate_total_max=config.l5_call_rate_total_max,
        l5_call_rate_batch_max=config.l5_call_rate_batch_max,
        l5_calls_per_min_per_lang_max=config.l5_calls_per_min_per_lang_max,
        l5_tokens_per_10sec_global_max=config.l5_tokens_per_10sec_global_max,
        l5_tokens_per_10sec_per_lang_max=config.l5_tokens_per_10sec_per_lang_max,
        l5_tokens_per_10sec_per_workspace_max=config.l5_tokens_per_10sec_per_workspace_max,
        l3_query_compile_cache_enabled=config.l3_query_compile_cache_enabled,
        l3_query_compile_ms_budget=config.l3_query_compile_ms_budget,
        l3_query_budget_ms=config.l3_query_budget_ms,
        l3_asset_mode=config.l3_asset_mode,
        l3_asset_lang_allowlist=config.l3_asset_lang_allowlist,
        l5_db_short_circuit_enabled=config.l5_db_short_circuit_enabled,
        l5_db_short_circuit_log_miss_reason=config.l5_db_short_circuit_log_miss_reason,
        tool_layer_repo=tool_layer_repo,
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
    stabilization_service = StabilizationService(enabled=config.stabilization_enabled)
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
            http_bg_proxy_enabled=config.http_bg_proxy_enabled,
            http_bg_proxy_target=config.http_bg_proxy_target,
        )
    )
    app.state.mcp_server = McpServer(db_path=db_path)
    stop_event = threading.Event()
    shutdown_reason: dict[str, str] = {"value": "NORMAL_SHUTDOWN"}

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
        while not stop_event.is_set():
            try:
                if os.getenv("SARI_TEST_AUTO_LOOP_FAIL", "").strip() == "1":
                    raise RuntimeError("auto loop failpoint")
                if not _is_parent_alive(launch_parent_pid, detached_mode=detached_mode):
                    shutdown_reason["value"] = "ORPHAN_SELF_TERMINATE"
                    runtime_repo.mark_exit_reason(this_pid, "ORPHAN_SELF_TERMINATE", now_iso8601_utc())
                    stop_event.set()
                    os.kill(this_pid, signal.SIGTERM)
                    return
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
                    if _is_fatal_db_error(event_exc):
                        _trigger_fatal_shutdown(
                            stop_event=stop_event,
                            runtime_repo=runtime_repo,
                            pid=this_pid,
                            reason="DB_FATAL_EVENT_RECORD",
                            shutdown_reason=shutdown_reason,
                        )
                        return
                    raise DaemonError(
                        ErrorContext(
                            code="ERR_DAEMON_EVENT_RECORD_FAILED",
                            message=f"자동제어 실패 이벤트 저장 실패: {event_exc}",
                        )
                    ) from event_exc
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
            tick_wait = min(float(config.pipeline_auto_tick_interval_sec), float(config.orphan_ppid_check_interval_sec))
            stop_event.wait(timeout=tick_wait)

    file_collection_service.start_background()
    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    auto_thread = threading.Thread(target=_auto_loop, daemon=True)
    heartbeat_thread.start()
    auto_thread.start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="error")
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=float(config.shutdown_join_timeout_sec))
        auto_thread.join(timeout=float(config.shutdown_join_timeout_sec))
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
