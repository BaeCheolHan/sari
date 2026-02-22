"""CLI 엔트리포인트를 제공한다."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import click

from sari.core.config import AppConfig
from sari.core.exceptions import BenchmarkError, DaemonError, PerfError, QualityError, SariBaseError, WorkspaceError
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.daemon_registry_repository import DaemonRegistryRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.pipeline_benchmark_repository import PipelineBenchmarkRepository
from sari.db.repositories.pipeline_perf_repository import PipelinePerfRepository
from sari.db.repositories.pipeline_quality_repository import PipelineQualityRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.pipeline_error_event_repository import PipelineErrorEventRepository
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.pipeline_lsp_matrix_repository import PipelineLspMatrixRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.migration import ensure_migrated
from sari.db.schema import init_schema
from sari.mcp.proxy import run_stdio_proxy
from sari.mcp.server import run_stdio
from sari.services.admin_service import AdminService
from sari.services.daemon_service import DaemonService
from sari.services.file_collection_service import build_default_file_collection_service
from sari.services.pipeline_benchmark_service import BenchmarkLspExtractionBackend, PipelineBenchmarkService
from sari.services.pipeline_perf_service import PipelinePerfService
from sari.services.pipeline_quality_service import PipelineQualityService, SerenaGoldenBackend
from sari.services.pipeline_control_service import PipelineControlService
from sari.services.language_probe_service import LanguageProbeService
from sari.services.lsp_matrix_diagnose_service import LspMatrixDiagnoseService
from sari.services.pipeline_lsp_matrix_service import PipelineLspMatrixService
from sari.services.workspace_service import WorkspaceService
from sari.lsp.hub import LspHub


@click.group(invoke_without_command=True)
@click.option("--transport", type=click.Choice(["stdio"], case_sensitive=False), required=False, default=None)
@click.option("--format", "mcp_format", type=click.Choice(["pack", "json"], case_sensitive=False), required=False, default=None)
@click.pass_context
def cli(ctx: click.Context, transport: str | None, mcp_format: str | None) -> None:
    """sari-v2 명령을 그룹화한다."""
    selected_transport = transport.lower() if isinstance(transport, str) else None
    if ctx.invoked_subcommand is not None:
        return
    # 한 줄 설정(command="sari") 호환을 위해 비대화형 stdin에서는 MCP stdio로 자동 진입한다.
    if selected_transport is None and not sys.stdin.isatty():
        selected_transport = "stdio"
    if selected_transport == "stdio":
        if isinstance(mcp_format, str) and mcp_format.strip() != "":
            os.environ["SARI_FORMAT"] = mcp_format.lower().strip()
        config = AppConfig.default()
        workspace_root_raw = os.getenv("SARI_WORKSPACE_ROOT", "").strip()
        workspace_root = workspace_root_raw if workspace_root_raw != "" else None
        raise SystemExit(
            run_stdio_proxy(
                db_path=config.db_path,
                workspace_root=workspace_root,
            )
        )
    click.echo(ctx.get_help())


@dataclass(frozen=True)
class CliServiceBundle:
    """CLI가 공유하는 서비스 집합 DTO다."""

    workspace_service: WorkspaceService
    daemon_service: DaemonService
    admin_service: AdminService
    pipeline_control_service: PipelineControlService
    pipeline_benchmark_service: PipelineBenchmarkService
    pipeline_perf_service: PipelinePerfService
    pipeline_quality_service: PipelineQualityService
    language_probe_service: LanguageProbeService
    pipeline_lsp_matrix_service: PipelineLspMatrixService
    lsp_matrix_diagnose_service: LspMatrixDiagnoseService


def _build_services() -> CliServiceBundle:
    """CLI에서 공통으로 사용할 서비스를 생성한다."""
    config = AppConfig.default()
    init_schema(config.db_path)
    ensure_migrated(config.db_path)
    workspace_repo = WorkspaceRepository(config.db_path)
    runtime_repo = RuntimeRepository(config.db_path)
    daemon_registry_repo = DaemonRegistryRepository(config.db_path)
    symbol_cache_repo = SymbolCacheRepository(config.db_path)
    queue_repo = FileEnrichQueueRepository(config.db_path)
    policy_repo = PipelinePolicyRepository(config.db_path)
    control_state_repo = PipelineControlStateRepository(config.db_path)
    event_repo = PipelineJobEventRepository(config.db_path)
    error_event_repo = PipelineErrorEventRepository(config.db_path)
    file_repo = FileCollectionRepository(config.db_path)
    body_repo = FileBodyRepository(config.db_path)
    lsp_repo = LspToolDataRepository(config.db_path)
    readiness_repo = ToolReadinessRepository(config.db_path)
    benchmark_file_collection_service = build_default_file_collection_service(
        workspace_repo=workspace_repo,
        file_repo=file_repo,
        enrich_queue_repo=queue_repo,
        body_repo=body_repo,
        lsp_repo=lsp_repo,
        readiness_repo=readiness_repo,
        policy_repo=policy_repo,
        event_repo=event_repo,
        error_event_repo=error_event_repo,
        run_mode=config.run_mode,
        lsp_backend=BenchmarkLspExtractionBackend(),
        persist_body_for_read=False,
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
        lsp_session_broker_enabled=config.lsp_session_broker_enabled,
        lsp_session_broker_metrics_enabled=config.lsp_session_broker_metrics_enabled,
        lsp_hotness_event_window_sec=config.lsp_hotness_event_window_sec,
        lsp_hotness_decay_window_sec=config.lsp_hotness_decay_window_sec,
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
    )
    perf_file_collection_service = build_default_file_collection_service(
        workspace_repo=workspace_repo,
        file_repo=file_repo,
        enrich_queue_repo=queue_repo,
        body_repo=body_repo,
        lsp_repo=lsp_repo,
        readiness_repo=readiness_repo,
        policy_repo=policy_repo,
        event_repo=event_repo,
        error_event_repo=error_event_repo,
        run_mode=config.run_mode,
        lsp_backend=None,
        persist_body_for_read=False,
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
        lsp_session_broker_enabled=config.lsp_session_broker_enabled,
        lsp_session_broker_metrics_enabled=config.lsp_session_broker_metrics_enabled,
        lsp_hotness_event_window_sec=config.lsp_hotness_event_window_sec,
        lsp_hotness_decay_window_sec=config.lsp_hotness_decay_window_sec,
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
    )
    benchmark_repo = PipelineBenchmarkRepository(config.db_path)
    perf_repo = PipelinePerfRepository(config.db_path)
    quality_repo = PipelineQualityRepository(config.db_path)
    language_probe_repo = LanguageProbeRepository(config.db_path)
    lsp_matrix_repo = PipelineLspMatrixRepository(config.db_path)
    quality_hub = LspHub(
        request_timeout_sec=config.lsp_request_timeout_sec,
        max_instances_per_repo_language=config.lsp_max_instances_per_repo_language,
        bulk_mode_enabled=config.lsp_bulk_mode_enabled,
        bulk_max_instances_per_repo_language=config.lsp_bulk_max_instances_per_repo_language,
        interactive_reserved_slots_per_repo_language=config.lsp_interactive_reserved_slots_per_repo_language,
        interactive_timeout_sec=config.lsp_interactive_timeout_sec,
        lsp_global_soft_limit=config.lsp_global_soft_limit,
        scale_out_hot_hits=config.lsp_scale_out_hot_hits,
        file_buffer_idle_ttl_sec=config.lsp_file_buffer_idle_ttl_sec,
        file_buffer_max_open=config.lsp_file_buffer_max_open,
        java_min_major=config.lsp_java_min_major,
        max_concurrent_starts=config.lsp_max_concurrent_starts,
        max_concurrent_l1_probes=config.lsp_max_concurrent_l1_probes,
    )
    probe_hub = LspHub(
        request_timeout_sec=config.lsp_request_timeout_sec,
        max_instances_per_repo_language=config.lsp_max_instances_per_repo_language,
        bulk_mode_enabled=config.lsp_bulk_mode_enabled,
        bulk_max_instances_per_repo_language=config.lsp_bulk_max_instances_per_repo_language,
        interactive_reserved_slots_per_repo_language=config.lsp_interactive_reserved_slots_per_repo_language,
        interactive_timeout_sec=config.lsp_interactive_timeout_sec,
        lsp_global_soft_limit=config.lsp_global_soft_limit,
        scale_out_hot_hits=config.lsp_scale_out_hot_hits,
        file_buffer_idle_ttl_sec=config.lsp_file_buffer_idle_ttl_sec,
        file_buffer_max_open=config.lsp_file_buffer_max_open,
        java_min_major=config.lsp_java_min_major,
        max_concurrent_starts=config.lsp_max_concurrent_starts,
        max_concurrent_l1_probes=config.lsp_max_concurrent_l1_probes,
    )
    language_probe_service = LanguageProbeService(
        workspace_repo=workspace_repo,
        lsp_hub=probe_hub,
        probe_repo=language_probe_repo,
        per_language_timeout_sec=config.lsp_probe_timeout_default_sec,
        per_language_timeout_overrides={"go": config.lsp_probe_timeout_go_sec},
        lsp_request_timeout_sec=config.lsp_request_timeout_sec,
        go_warmup_timeout_sec=config.lsp_probe_timeout_go_sec,
    )
    pipeline_benchmark_service = PipelineBenchmarkService(
        file_collection_service=benchmark_file_collection_service,
        queue_repo=queue_repo,
        lsp_repo=lsp_repo,
        policy_repo=policy_repo,
        benchmark_repo=benchmark_repo,
        artifact_root=config.db_path.parent / "artifacts",
    )
    return CliServiceBundle(
        workspace_service=WorkspaceService(workspace_repo),
        daemon_service=DaemonService(
            config,
            runtime_repo,
            workspace_repo=workspace_repo,
            registry_repo=daemon_registry_repo,
        ),
        admin_service=AdminService(
            config=config,
            workspace_repo=workspace_repo,
            runtime_repo=runtime_repo,
            symbol_cache_repo=symbol_cache_repo,
            queue_repo=queue_repo,
        ),
        pipeline_control_service=PipelineControlService(
            policy_repo=policy_repo,
            event_repo=event_repo,
            queue_repo=queue_repo,
            control_state_repo=control_state_repo,
        ),
        pipeline_benchmark_service=pipeline_benchmark_service,
        pipeline_perf_service=PipelinePerfService(
            file_collection_service=perf_file_collection_service,
            queue_repo=queue_repo,
            benchmark_service=pipeline_benchmark_service,
            perf_repo=perf_repo,
            artifact_root=config.db_path.parent / "artifacts",
        ),
        pipeline_quality_service=PipelineQualityService(
            file_repo=file_repo,
            lsp_repo=lsp_repo,
            quality_repo=quality_repo,
            golden_backend=SerenaGoldenBackend(hub=quality_hub),
            artifact_root=config.db_path.parent / "artifacts",
        ),
        language_probe_service=language_probe_service,
        pipeline_lsp_matrix_service=PipelineLspMatrixService(
            probe_service=language_probe_service,
            run_repo=lsp_matrix_repo,
        ),
        lsp_matrix_diagnose_service=LspMatrixDiagnoseService(),
    )


def _print_json(payload: dict[str, object], exit_code: int = 0) -> None:
    """JSON 응답을 출력하고 종료코드를 반영한다."""
    click.echo(json.dumps(payload, ensure_ascii=False))
    if exit_code != 0:
        raise SystemExit(exit_code)


@cli.group()
def roots() -> None:
    """워크스페이스 관리 명령 그룹이다."""


@roots.command("add")
@click.argument("path", type=click.Path(exists=False))
def roots_add(path: str) -> None:
    """워크스페이스를 추가한다."""
    services = _build_services()
    try:
        workspace = services.workspace_service.add_workspace(path)
    except WorkspaceError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return

    _print_json(
        {
            "workspace": {
                "path": workspace.path,
                "name": workspace.name,
                "indexed_at": workspace.indexed_at,
                "is_active": workspace.is_active,
            }
        }
    )


@roots.command("list")
def roots_list() -> None:
    """워크스페이스 목록을 출력한다."""
    services = _build_services()
    items = services.workspace_service.list_workspaces()
    _print_json(
        {
            "items": [
                {
                    "path": item.path,
                    "name": item.name,
                    "indexed_at": item.indexed_at,
                    "is_active": item.is_active,
                }
                for item in items
            ]
        }
    )


@roots.command("remove")
@click.argument("path", type=click.Path(exists=False))
def roots_remove(path: str) -> None:
    """워크스페이스를 삭제한다."""
    services = _build_services()
    services.workspace_service.remove_workspace(path)
    _print_json({"removed": str(Path(path).expanduser().resolve())})


@roots.command("activate")
@click.argument("path", type=click.Path(exists=False))
def roots_activate(path: str) -> None:
    """워크스페이스를 활성화한다."""
    services = _build_services()
    try:
        workspace = services.workspace_service.set_workspace_active(path, True)
    except WorkspaceError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json(
        {
            "workspace": {
                "path": workspace.path,
                "name": workspace.name,
                "indexed_at": workspace.indexed_at,
                "is_active": workspace.is_active,
            }
        }
    )


@roots.command("deactivate")
@click.argument("path", type=click.Path(exists=False))
def roots_deactivate(path: str) -> None:
    """워크스페이스를 비활성화한다."""
    services = _build_services()
    try:
        workspace = services.workspace_service.set_workspace_active(path, False)
    except WorkspaceError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json(
        {
            "workspace": {
                "path": workspace.path,
                "name": workspace.name,
                "indexed_at": workspace.indexed_at,
                "is_active": workspace.is_active,
            }
        }
    )


@cli.group()
def daemon() -> None:
    """데몬 관리 명령 그룹이다."""


@daemon.command("start")
@click.option("--run-mode", type=click.Choice(["dev", "prod"], case_sensitive=False), default="prod")
def daemon_start(run_mode: str) -> None:
    """데몬을 시작한다."""
    services = _build_services()
    try:
        runtime = services.daemon_service.start(run_mode=run_mode.lower())
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json(
        {
            "daemon": {
                "pid": runtime.pid,
                "host": runtime.host,
                "port": runtime.port,
                "state": runtime.state,
                "started_at": runtime.started_at,
                "session_count": runtime.session_count,
            }
        }
    )


@daemon.command("status")
def daemon_status() -> None:
    """데몬 상태를 조회한다."""
    services = _build_services()
    runtime = services.daemon_service.status()
    if runtime is None:
        _print_json({"daemon": None})
        return
    _print_json(
        {
            "daemon": {
                "pid": runtime.pid,
                "host": runtime.host,
                "port": runtime.port,
                "state": runtime.state,
                "started_at": runtime.started_at,
                "session_count": runtime.session_count,
            }
        }
    )


@daemon.command("stop")
def daemon_stop() -> None:
    """데몬을 종료한다."""
    services = _build_services()
    try:
        services.daemon_service.stop()
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"stopped": True})


@daemon.command("ensure")
@click.option("--run-mode", type=click.Choice(["dev", "prod"], case_sensitive=False), default="prod")
def daemon_ensure(run_mode: str) -> None:
    """데몬이 없으면 시작하고 있으면 기존 엔드포인트를 반환한다."""
    services = _build_services()
    runtime = services.daemon_service.status()
    if runtime is not None:
        _print_json(
            {
                "started": False,
                "daemon": {
                    "pid": runtime.pid,
                    "host": runtime.host,
                    "port": runtime.port,
                    "state": runtime.state,
                    "started_at": runtime.started_at,
                    "session_count": runtime.session_count,
                },
            }
        )
        return
    try:
        started = services.daemon_service.start(run_mode=run_mode.lower())
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json(
        {
            "started": True,
            "daemon": {
                "pid": started.pid,
                "host": started.host,
                "port": started.port,
                "state": started.state,
                "started_at": started.started_at,
                "session_count": started.session_count,
            },
        }
    )


@daemon.command("refresh")
def daemon_refresh() -> None:
    """운영 인덱스 갱신 작업을 트리거한다."""
    services = _build_services()
    _print_json(services.admin_service.index())


@cli.command("doctor")
def doctor_command() -> None:
    """런타임 진단 결과를 출력한다."""
    services = _build_services()
    checks = services.admin_service.doctor()
    _print_json(
        {
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                }
                for check in checks
            ]
        }
    )


@cli.command("index")
def index_command() -> None:
    """캐시 무효화 기반 인덱스 갱신을 수행한다."""
    services = _build_services()
    _print_json(services.admin_service.index())


@cli.command("install")
@click.option("--host", type=click.Choice(["codex", "gemini"], case_sensitive=False), required=True)
@click.option("--print", "print_only", is_flag=True, default=False)
def install_command(host: str, print_only: bool) -> None:
    """호스트용 MCP 설정 스니펫을 생성해 출력한다."""
    services = _build_services()
    if print_only:
        payload = services.admin_service.install_host_config(host.lower())
        if "error" in payload:
            _print_json(payload, exit_code=1)
            return
        _print_json(payload)
        return
    payload = services.admin_service.apply_host_config(host.lower())
    if "error" in payload:
        _print_json(payload, exit_code=1)
        return
    _print_json(payload)


@cli.group("lsp")
def lsp_group() -> None:
    """LSP 런타임/캐시 관리 명령 그룹이다."""


@lsp_group.command("reset-unavailable")
@click.option("--repo", type=str, default=None, help="특정 repo_root 범위만 초기화한다.")
@click.option("--lang", type=str, default=None, help="특정 언어만 초기화한다 (예: python, go, java).")
@click.option("--all", "reset_all", is_flag=True, default=False, help="전체 unavailable 캐시를 초기화한다.")
def lsp_reset_unavailable_command(repo: str | None, lang: str | None, reset_all: bool) -> None:
    """LSP unavailable cache를 수동으로 초기화한다."""
    if not reset_all and (repo is None or repo.strip() == ""):
        _print_json({"error": {"code": "ERR_REPO_REQUIRED", "message": "--repo 또는 --all 이 필요합니다"}}, exit_code=1)
        return
    services = _build_services()
    file_collection_service = services.pipeline_perf_service._file_collection_service  # type: ignore[attr-defined]
    resetter = getattr(file_collection_service, "reset_lsp_unavailable_cache", None)
    if not callable(resetter):
        _print_json({"error": {"code": "ERR_UNSUPPORTED", "message": "reset_lsp_unavailable_cache capability is required"}}, exit_code=1)
        return
    cleared = int(resetter(repo_root=None if reset_all else repo, language=lang))
    _print_json(
        {
            "lsp_unavailable_reset": {
                "scope": "all" if reset_all else ("repo_language" if lang else "repo"),
                "repo_root": None if reset_all else str(Path(repo).expanduser().resolve()) if isinstance(repo, str) and repo.strip() != "" else None,
                "language": lang,
                "cleared_count": cleared,
            }
        }
    )


@cli.group("engine")
def engine_group() -> None:
    """엔진 운영 명령 그룹이다."""


@engine_group.command("status")
def engine_status_command() -> None:
    """엔진 의존성 상태를 조회한다."""
    services = _build_services()
    _print_json(services.admin_service.engine_status())


@engine_group.command("install")
def engine_install_command() -> None:
    """엔진 설치 확인 작업을 수행한다."""
    services = _build_services()
    _print_json(services.admin_service.engine_install())


@engine_group.command("rebuild")
def engine_rebuild_command() -> None:
    """엔진 캐시 재빌드를 수행한다."""
    services = _build_services()
    _print_json(services.admin_service.engine_rebuild())


@engine_group.command("verify")
def engine_verify_command() -> None:
    """엔진 동작 검증 결과를 출력한다."""
    services = _build_services()
    payload = services.admin_service.engine_verify()
    if bool(payload.get("verified")):
        _print_json(payload)
        return
    _print_json(payload, exit_code=1)


@cli.group()
def mcp() -> None:
    """MCP 명령 그룹이다."""


@mcp.command("stdio")
@click.option("--local", is_flag=True, default=False, help="로컬 MCP 서버를 직접 실행한다.")
@click.option("--workspace-root", type=click.Path(exists=False), default=None)
@click.option("--host", type=str, default=None)
@click.option("--port", type=int, default=None)
@click.option("--timeout-sec", type=float, default=2.0)
def mcp_stdio(local: bool, workspace_root: str | None, host: str | None, port: int | None, timeout_sec: float) -> None:
    """MCP stdio를 실행한다. 기본은 daemon proxy 경로다."""
    config = AppConfig.default()
    if not local:
        raise SystemExit(
            run_stdio_proxy(
                db_path=config.db_path,
                workspace_root=workspace_root,
                host_override=host,
                port_override=port,
                timeout_sec=timeout_sec,
            )
        )
    raise SystemExit(run_stdio(config.db_path))


@mcp.command("proxy")
@click.option("--workspace-root", type=click.Path(exists=False), default=None)
@click.option("--host", type=str, default=None)
@click.option("--port", type=int, default=None)
@click.option("--timeout-sec", type=float, default=2.0)
def mcp_proxy(workspace_root: str | None, host: str | None, port: int | None, timeout_sec: float) -> None:
    """MCP stdio 요청을 daemon endpoint로 중계한다."""
    config = AppConfig.default()
    raise SystemExit(
        run_stdio_proxy(
            db_path=config.db_path,
            workspace_root=workspace_root,
            host_override=host,
            port_override=port,
            timeout_sec=timeout_sec,
        )
    )


@cli.group("pipeline")
def pipeline_group() -> None:
    """파이프라인 운영 명령 그룹이다."""


@pipeline_group.group("policy")
def pipeline_policy_group() -> None:
    """파이프라인 정책 명령 그룹이다."""


@pipeline_policy_group.command("show")
def pipeline_policy_show_command() -> None:
    """현재 파이프라인 정책을 조회한다."""
    services = _build_services()
    _print_json({"policy": services.pipeline_control_service.get_policy().to_dict()})


@pipeline_policy_group.command("set")
@click.option("--deletion-hold", type=click.Choice(["on", "off"], case_sensitive=False), required=False)
@click.option("--l3-p95-threshold-ms", type=int, required=False)
@click.option("--dead-ratio-bps", type=int, required=False)
@click.option("--workers", type=int, required=False)
@click.option("--bootstrap-mode-enabled", type=click.Choice(["on", "off"], case_sensitive=False), required=False)
@click.option("--bootstrap-l3-worker-count", type=int, required=False)
@click.option("--bootstrap-l3-queue-max", type=int, required=False)
@click.option("--bootstrap-exit-min-l2-coverage-bps", type=int, required=False)
@click.option("--bootstrap-exit-max-sec", type=int, required=False)
@click.option("--alert-window-sec", type=int, required=False)
def pipeline_policy_set_command(
    deletion_hold: str | None,
    l3_p95_threshold_ms: int | None,
    dead_ratio_bps: int | None,
    workers: int | None,
    bootstrap_mode_enabled: str | None,
    bootstrap_l3_worker_count: int | None,
    bootstrap_l3_queue_max: int | None,
    bootstrap_exit_min_l2_coverage_bps: int | None,
    bootstrap_exit_max_sec: int | None,
    alert_window_sec: int | None,
) -> None:
    """파이프라인 정책을 갱신한다."""
    services = _build_services()
    hold_value: bool | None = None
    if deletion_hold is not None:
        hold_value = deletion_hold.lower() == "on"
    bootstrap_mode_value: bool | None = None
    if bootstrap_mode_enabled is not None:
        bootstrap_mode_value = bootstrap_mode_enabled.lower() == "on"
    try:
        updated = services.pipeline_control_service.update_policy(
            deletion_hold=hold_value,
            l3_p95_threshold_ms=l3_p95_threshold_ms,
            dead_ratio_threshold_bps=dead_ratio_bps,
            enrich_worker_count=workers,
            bootstrap_mode_enabled=bootstrap_mode_value,
            bootstrap_l3_worker_count=bootstrap_l3_worker_count,
            bootstrap_l3_queue_max=bootstrap_l3_queue_max,
            bootstrap_exit_min_l2_coverage_bps=bootstrap_exit_min_l2_coverage_bps,
            bootstrap_exit_max_sec=bootstrap_exit_max_sec,
            alert_window_sec=alert_window_sec,
        )
    except SariBaseError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"policy": updated.to_dict()})


@pipeline_group.group("alert")
def pipeline_alert_group() -> None:
    """파이프라인 알람 명령 그룹이다."""


@pipeline_alert_group.command("status")
def pipeline_alert_status_command() -> None:
    """파이프라인 알람 상태를 조회한다."""
    services = _build_services()
    _print_json({"alert": services.pipeline_control_service.get_alert_status().to_dict()})


@pipeline_group.group("dead")
def pipeline_dead_group() -> None:
    """DEAD 작업 관리 명령 그룹이다."""


@pipeline_dead_group.command("list")
@click.option("--repo", type=str, required=True)
@click.option("--limit", type=int, default=20, show_default=True)
def pipeline_dead_list_command(repo: str, limit: int) -> None:
    """DEAD 작업 목록을 조회한다."""
    services = _build_services()
    try:
        items = services.pipeline_control_service.list_dead_jobs(repo_root=repo, limit=limit)
    except SariBaseError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json(
        {
            "items": [item.to_dict() for item in items],
            "meta": {
                "queue_snapshot": services.pipeline_control_service.get_queue_snapshot(),
                "executed_at": datetime.now(timezone.utc).isoformat(),
                "repo_scope": "repo",
            },
        }
    )


@pipeline_dead_group.command("requeue")
@click.option("--repo", type=str, required=True)
@click.option("--limit", type=int, default=20, show_default=True)
def pipeline_dead_requeue_command(repo: str, limit: int) -> None:
    """DEAD 작업을 재큐잉한다."""
    services = _build_services()
    try:
        result = services.pipeline_control_service.requeue_dead_jobs(repo_root=repo, limit=limit)
    except SariBaseError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"result": result.to_dict(), "meta": {"queue_snapshot": result.queue_snapshot, "executed_at": result.executed_at, "repo_scope": result.repo_scope}})


@pipeline_dead_group.command("purge")
@click.option("--repo", type=str, required=True)
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--confirm", is_flag=True, default=False)
def pipeline_dead_purge_command(repo: str, limit: int, confirm: bool) -> None:
    """DEAD 작업을 영구 삭제한다."""
    if not confirm:
        _print_json({"error": {"code": "ERR_DEAD_JOB_ACTION_INVALID", "message": "--confirm required"}}, exit_code=1)
        return
    services = _build_services()
    try:
        result = services.pipeline_control_service.purge_dead_jobs(repo_root=repo, limit=limit)
    except SariBaseError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"result": result.to_dict(), "meta": {"queue_snapshot": result.queue_snapshot, "executed_at": result.executed_at, "repo_scope": result.repo_scope}})


@pipeline_group.group("auto")
def pipeline_auto_group() -> None:
    """파이프라인 자동제어 명령 그룹이다."""


@pipeline_auto_group.command("status")
def pipeline_auto_status_command() -> None:
    """자동제어 상태를 조회한다."""
    services = _build_services()
    _print_json({"auto_control": services.pipeline_control_service.get_auto_control_state().to_dict()})


@pipeline_auto_group.command("set")
@click.option("--enabled", type=click.Choice(["on", "off"], case_sensitive=False), required=True)
def pipeline_auto_set_command(enabled: str) -> None:
    """자동제어 활성 여부를 설정한다."""
    services = _build_services()
    updated = services.pipeline_control_service.set_auto_hold_enabled(enabled.lower() == "on")
    _print_json({"auto_control": updated.to_dict()})


@pipeline_auto_group.command("tick")
def pipeline_auto_tick_command() -> None:
    """자동제어 평가를 1회 수행한다."""
    services = _build_services()
    _print_json(services.pipeline_control_service.evaluate_auto_hold())


@pipeline_group.group("benchmark")
def pipeline_benchmark_group() -> None:
    """파이프라인 벤치마크 명령 그룹이다."""


@pipeline_benchmark_group.command("run")
@click.option("--repo", type=str, required=True)
@click.option("--target-files", type=int, default=50_000, show_default=True)
@click.option("--profile", type=str, default="default", show_default=True)
@click.option("--language-filter", type=str, multiple=True)
@click.option("--per-language-report", is_flag=True, default=False)
def pipeline_benchmark_run_command(
    repo: str,
    target_files: int,
    profile: str,
    language_filter: tuple[str, ...],
    per_language_report: bool,
) -> None:
    """벤치마크를 실행하고 요약 결과를 출력한다."""
    services = _build_services()
    try:
        summary = services.pipeline_benchmark_service.run(
            repo_root=repo,
            target_files=target_files,
            profile=profile,
            language_filter=(None if len(language_filter) == 0 else tuple(language_filter)),
            per_language_report=per_language_report,
        )
    except BenchmarkError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"benchmark": summary})


@pipeline_benchmark_group.command("report")
@click.option("--latest", is_flag=True, default=False)
def pipeline_benchmark_report_command(latest: bool) -> None:
    """최신 벤치마크 리포트를 출력한다."""
    del latest
    services = _build_services()
    try:
        summary = services.pipeline_benchmark_service.get_latest_report()
    except BenchmarkError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"benchmark": summary})


@pipeline_group.group("perf")
def pipeline_perf_group() -> None:
    """파이프라인 성능 실측 명령 그룹이다."""


@pipeline_perf_group.command("run")
@click.option("--repo", type=str, required=True)
@click.option("--target-files", type=int, default=2_000, show_default=True)
@click.option("--profile", type=str, default="realistic_v1", show_default=True)
@click.option("--dataset-mode", type=click.Choice(["isolated", "legacy"], case_sensitive=False), default="isolated", show_default=True)
@click.option("--fresh-db", is_flag=True, default=False, help="측정 시작 전 런타임 상태를 논리 초기화한다(DB 파일 재생성 아님).")
@click.option("--reset-probe-state", is_flag=True, default=False, help="측정 시작 전 probe 상태를 초기화한다.")
@click.option("--cold-lsp-reset", is_flag=True, default=False, help="측정 시작 전 LSP 런타임을 종료해 cold-start로 측정한다.")
@click.option("--workspace-exclude-glob", type=str, multiple=True, default=())
def pipeline_perf_run_command(
    repo: str,
    target_files: int,
    profile: str,
    dataset_mode: str,
    fresh_db: bool,
    reset_probe_state: bool,
    cold_lsp_reset: bool,
    workspace_exclude_glob: tuple[str, ...],
) -> None:
    """혼합지표 성능 실측을 실행하고 요약 결과를 출력한다."""
    services = _build_services()
    try:
        summary = services.pipeline_perf_service.run(
            repo_root=repo,
            target_files=target_files,
            profile=profile,
            dataset_mode=dataset_mode.lower(),
            fresh_db=fresh_db,
            reset_probe_state=reset_probe_state,
            cold_lsp_reset=cold_lsp_reset,
            workspace_exclude_globs=tuple(workspace_exclude_glob),
        )
    except PerfError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"perf": summary})


@pipeline_perf_group.command("report")
@click.option("--repo", type=str, required=True)
def pipeline_perf_report_command(repo: str) -> None:
    """최신 성능 실측 리포트를 출력한다."""
    del repo
    services = _build_services()
    try:
        summary = services.pipeline_perf_service.get_latest_report()
    except PerfError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"perf": summary})


@pipeline_group.group("quality")
def pipeline_quality_group() -> None:
    """파이프라인 품질 명령 그룹이다."""


@pipeline_quality_group.command("run")
@click.option("--repo", type=str, required=True)
@click.option("--limit-files", type=int, default=2_000, show_default=True)
@click.option("--profile", type=str, default="default", show_default=True)
@click.option("--language-filter", type=str, multiple=True)
def pipeline_quality_run_command(repo: str, limit_files: int, profile: str, language_filter: tuple[str, ...]) -> None:
    """품질 평가를 실행하고 요약 결과를 출력한다."""
    services = _build_services()
    try:
        summary = services.pipeline_quality_service.run(
            repo_root=repo,
            limit_files=limit_files,
            profile=profile,
            language_filter=(None if len(language_filter) == 0 else tuple(language_filter)),
        )
    except QualityError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"quality": summary})


@pipeline_quality_group.command("report")
@click.option("--repo", type=str, required=True)
def pipeline_quality_report_command(repo: str) -> None:
    """최신 품질 리포트를 출력한다."""
    services = _build_services()
    try:
        summary = services.pipeline_quality_service.get_latest_report(repo_root=repo)
    except QualityError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"quality": summary})


@pipeline_group.group("lsp-matrix")
def pipeline_lsp_matrix_group() -> None:
    """LSP 언어 readiness 매트릭스 명령 그룹이다."""


@pipeline_lsp_matrix_group.command("run")
@click.option("--repo", type=str, required=True)
@click.option("--required-language", "required_languages", type=str, multiple=True)
@click.option("--fail-on-unavailable", type=click.Choice(["true", "false"], case_sensitive=False), default="true", show_default=True)
@click.option("--strict-all-languages", type=click.Choice(["true", "false"], case_sensitive=False), default="true", show_default=True)
@click.option("--strict-symbol-gate", type=click.Choice(["true", "false"], case_sensitive=False), default="true", show_default=True)
def pipeline_lsp_matrix_run_command(
    repo: str,
    required_languages: tuple[str, ...],
    fail_on_unavailable: str,
    strict_all_languages: str,
    strict_symbol_gate: str,
) -> None:
    """언어별 LSP readiness를 점검하고 결과를 출력한다."""
    services = _build_services()
    fail_on_unavailable_bool = fail_on_unavailable.strip().lower() == "true"
    strict_all_languages_bool = strict_all_languages.strip().lower() == "true"
    strict_symbol_gate_bool = strict_symbol_gate.strip().lower() == "true"
    try:
        result = services.pipeline_lsp_matrix_service.run(
            repo_root=repo,
            required_languages=(None if len(required_languages) == 0 else tuple(required_languages)),
            fail_on_unavailable=fail_on_unavailable_bool,
            strict_all_languages=strict_all_languages_bool,
            strict_symbol_gate=strict_symbol_gate_bool,
        )
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"lsp_matrix": result})


@pipeline_lsp_matrix_group.command("report")
@click.option("--repo", type=str, required=True)
def pipeline_lsp_matrix_report_command(repo: str) -> None:
    """최신 LSP readiness 매트릭스 리포트를 출력한다."""
    services = _build_services()
    try:
        result = services.pipeline_lsp_matrix_service.get_latest_report(repo_root=repo)
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"lsp_matrix": result})


@pipeline_lsp_matrix_group.command("diagnose")
@click.option("--repo", type=str, required=True)
@click.option("--mode", type=click.Choice(["latest", "run"], case_sensitive=False), default="latest", show_default=True)
@click.option("--output-dir", type=click.Path(exists=False, file_okay=False, dir_okay=True), default="artifacts/ci", show_default=True)
def pipeline_lsp_matrix_diagnose_command(repo: str, mode: str, output_dir: str) -> None:
    """LSP 매트릭스 결과를 진단 리포트로 출력/저장한다."""
    services = _build_services()
    normalized_mode = mode.strip().lower()
    try:
        if normalized_mode == "run":
            matrix_report = services.pipeline_lsp_matrix_service.run(
                repo_root=repo,
                required_languages=None,
                fail_on_unavailable=False,
                strict_all_languages=True,
                strict_symbol_gate=True,
            )
        else:
            matrix_report = services.pipeline_lsp_matrix_service.get_latest_report(repo_root=repo)
        diagnosis = services.lsp_matrix_diagnose_service.diagnose(matrix_report=matrix_report)
        json_path, md_path = services.lsp_matrix_diagnose_service.write_outputs(
            diagnosis=diagnosis,
            output_dir=Path(output_dir).expanduser().resolve(),
        )
    except DaemonError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)
        return
    _print_json({"diagnose": diagnosis, "artifacts": {"json": str(json_path), "markdown": str(md_path)}})


def main() -> None:
    """CLI 실행 진입점이다."""
    try:
        cli()
    except SariBaseError as exc:
        _print_json({"error": asdict(exc.context)}, exit_code=1)


if __name__ == "__main__":
    main()
