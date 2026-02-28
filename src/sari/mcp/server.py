from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import BinaryIO
from sari import __version__ as SARI_VERSION
from sari.core.config import AppConfig
from sari.core.composition import build_file_collection_service_from_config, build_lsp_hub, build_repository_bundle, build_search_stack
from sari.core.exceptions import DaemonError, ErrorContext, ValidationError
from sari.core.models import ErrorResponseDTO
from sari.lsp.hub import LspHub
from sari.mcp.contracts import McpError, McpResponse
from sari.mcp.daemon_forward_policy import (
    StartDaemonFn,
    default_start_daemon,
    resolve_target,
)
from sari.mcp.daemon_router import DaemonRouter, DaemonRouterConfig
from sari.mcp.tools.admin_tools import DoctorTool, RepoCandidatesTool, RescanTool
from sari.mcp.tool_visibility import filter_tools_list_response_payload, is_hidden_tool_name
from sari.mcp.tools.pipeline_admin_tools import PipelineAutoSetTool, PipelineAutoStatusTool, PipelineAutoTickTool, PipelineAlertStatusTool, PipelineDeadListTool, PipelineDeadPurgeTool, PipelineDeadRequeueTool, PipelinePolicyGetTool, PipelinePolicySetTool
from sari.mcp.tools.pipeline_perf_tools import PipelinePerfReportTool, PipelinePerfRunTool
from sari.mcp.tools.pipeline_lsp_matrix_tools import PipelineLspMatrixReportTool, PipelineLspMatrixRunTool
from sari.mcp.tools.pipeline_quality_tools import PipelineQualityReportTool, PipelineQualityRunTool
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.pack1_line import PackLineOptionsDTO, render_pack_v2
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.tools.file_collection_tools import IndexFileTool, ListFilesTool, ReadFileTool, ScanOnceTool
from sari.mcp.tools_catalog import build_tools_list_result_payload
from sari.mcp.tool_registry import build_tool_handler_attr_map
from sari.mcp.tools.symbol_tools import GetCallersTool, SearchSymbolTool
from sari.mcp.tools.arg_normalizer import ArgNormalizationError, normalize_tool_arguments
from sari.mcp.transport import MCP_MODE_FRAMED, McpTransport, McpTransportParseError
from sari.mcp.server_daemon_forward import forward_once
from sari.mcp.tools.search_tool import SearchTool
from sari.mcp.tools.knowledge_tools import ArchiveContextTool, GetContextTool, GetSnippetTool, KnowledgeTool, SaveSnippetTool
from sari.mcp.tools.read_tool import DryRunDiffTool, ReadTool
from sari.mcp.tools.sari_guide_tool import SariGuideTool
from sari.mcp.tools.status_tool import StatusTool
from sari.mcp.tools.symbol_graph_tools import CallGraphHealthTool, CallGraphTool, GetImplementationsTool, ListSymbolsTool, ReadSymbolTool
from sari.search.orchestrator import RankingBlendConfigDTO, SearchOrchestrator
from sari.services.admin import AdminService
from sari.services.collection.service import SolidLspExtractionBackend
from sari.services.pipeline.perf_service import PipelinePerfService
from sari.services.pipeline.control_service import PipelineControlService
from sari.services.language_probe.service import LanguageProbeService
from sari.services.pipeline.lsp_matrix_service import PipelineLspMatrixService
from sari.services.pipeline.quality_service import PipelineQualityService, SerenaGoldenBackend

MAX_CONSECUTIVE_INVALID_FRAMES = 3

class McpServer:
    _TOOLS_SCHEMA_VERSION = "2026-02-18.pack1.v2-line"
    _DEFAULT_PROTOCOL_VERSION = '2024-11-05'
    _SUPPORTED_PROTOCOL_VERSIONS = (
        '2025-11-25',
        '2025-06-18',
        '2025-03-26',
        '2024-11-05',
    )

    def __init__(self, db_path: Path) -> None:
        runtime_config = AppConfig.default()
        repos = build_repository_bundle(db_path)
        self._runtime_config = runtime_config
        self._db_path = db_path
        self._proxy_to_daemon = runtime_config.mcp_forward_to_daemon
        self._daemon_autostart_on_failure = runtime_config.mcp_daemon_autostart
        self._daemon_forward_timeout_sec = _parse_daemon_forward_timeout(str(runtime_config.mcp_daemon_timeout_sec))
        self._daemon_start_fn: StartDaemonFn = default_start_daemon
        self._daemon_router = DaemonRouter(
            db_path=db_path,
            config=DaemonRouterConfig(
                proxy_to_daemon=self._proxy_to_daemon,
                auto_start_on_failure=self._daemon_autostart_on_failure,
                timeout_sec=self._daemon_forward_timeout_sec,
            ),
            start_daemon_fn=self._daemon_start_fn,
            resolve_target_fn=resolve_target,
            forward_once_fn=forward_once,
        )
        self._closed = False
        self._managed_lsp_hubs: list[LspHub] = []
        lsp_hub_config = runtime_config.lsp_hub_config()
        search_config = runtime_config.search_config()
        collection_config = runtime_config.collection_config()
        workspace_repo = repos.workspace_repo
        self._workspace_repo = workspace_repo
        runtime_repo = repos.runtime_repo
        self._runtime_repo = runtime_repo
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
        shared_hub = build_lsp_hub(lsp_hub_config)
        self._managed_lsp_hubs.append(shared_hub)
        search_stack = build_search_stack(
            search_config=search_config,
            repos=repos,
            lsp_hub=shared_hub,
            candidate_backend="tantivy",
            candidate_fallback_scan=True,
            candidate_allowed_suffixes=collection_config.include_ext,
            blend_config_version="v2-config",
        )
        candidate_service = search_stack.candidate_service
        vector_sink = search_stack.vector_sink
        file_collection_service = build_file_collection_service_from_config(
            config=runtime_config,
            repos=repos,
            lsp_backend=SolidLspExtractionBackend(
                shared_hub,
                probe_workers=runtime_config.lsp_probe_workers,
                l1_workers=runtime_config.lsp_probe_l1_workers,
                force_join_ms=runtime_config.lsp_probe_force_join_ms,
                warming_retry_sec=runtime_config.lsp_probe_warming_retry_sec,
                warming_threshold=runtime_config.lsp_probe_warming_threshold,
                permanent_backoff_sec=runtime_config.lsp_probe_permanent_backoff_sec,
                symbol_normalizer_executor_mode=runtime_config.l5_symbol_normalizer_executor_mode,
                symbol_normalizer_subinterp_workers=runtime_config.l5_symbol_normalizer_subinterp_workers,
                symbol_normalizer_subinterp_min_symbols=runtime_config.l5_symbol_normalizer_subinterp_min_symbols,
            ),
            run_mode='prod',
            candidate_index_sink=candidate_service,
            vector_index_sink=vector_sink,
        )

        perf_service = PipelinePerfService(
            file_collection_service=file_collection_service,
            queue_repo=enrich_queue_repo,
            perf_repo=perf_repo,
            artifact_root=db_path.parent / "artifacts",
            stage_baseline_repo=stage_baseline_repo,
        )
        quality_service = PipelineQualityService(
            file_repo=file_repo,
            lsp_repo=lsp_repo,
            quality_repo=quality_repo,
            golden_backend=SerenaGoldenBackend(hub=shared_hub),
            artifact_root=db_path.parent / 'artifacts',
            tool_layer_repo=tool_layer_repo,
        )
        language_probe_service = LanguageProbeService(
            workspace_repo=workspace_repo,
            lsp_hub=shared_hub,
            probe_repo=language_probe_repo,
            per_language_timeout_sec=runtime_config.lsp_probe_timeout_default_sec,
            per_language_timeout_overrides={"go": runtime_config.lsp_probe_timeout_go_sec},
            lsp_request_timeout_sec=runtime_config.lsp_request_timeout_sec,
            go_warmup_timeout_sec=runtime_config.lsp_probe_timeout_go_sec,
        )
        pipeline_lsp_matrix_service = PipelineLspMatrixService(probe_service=language_probe_service, run_repo=lsp_matrix_repo)
        pipeline_control_service = PipelineControlService(policy_repo=policy_repo, event_repo=event_repo, queue_repo=enrich_queue_repo, control_state_repo=control_state_repo)
        admin_service = AdminService(
            config=AppConfig(db_path=db_path, host='127.0.0.1', preferred_port=47777, max_port_scan=50, stop_grace_sec=10),
            workspace_repo=workspace_repo,
            runtime_repo=runtime_repo,
            symbol_cache_repo=symbol_cache_repo,
            queue_repo=enrich_queue_repo,
            lsp_reconciler=shared_hub.reconcile_runtime,
        )
        orchestrator = search_stack.orchestrator
        stabilization_service = StabilizationService()
        self._file_collection_service = file_collection_service
        self._search_tool = SearchTool(
            orchestrator=orchestrator,
            workspace_repo=workspace_repo,
            tool_layer_repo=tool_layer_repo,
            metrics_provider=file_collection_service.get_pipeline_metrics,
            repo_registry_repo=repo_registry_repo,
            stabilization_service=stabilization_service,
            include_info_default=runtime_config.lsp_include_info_default,
            symbol_info_budget_sec_default=runtime_config.lsp_symbol_info_budget_sec,
        )
        self._doctor_tool = DoctorTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._rescan_tool = RescanTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._repo_candidates_tool = RepoCandidatesTool(admin_service=admin_service, workspace_repo=workspace_repo)
        self._scan_once_tool = ScanOnceTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._list_files_tool = ListFilesTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._read_file_tool = ReadFileTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._index_file_tool = IndexFileTool(workspace_repo=workspace_repo, collection_service=file_collection_service)
        self._search_symbol_tool = SearchSymbolTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._get_callers_tool = GetCallersTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._sari_guide_tool = SariGuideTool()
        self._status_tool = StatusTool(
            workspace_repo=workspace_repo,
            runtime_repo=runtime_repo,
            file_repo=file_repo,
            lsp_repo=lsp_repo,
            language_probe_repo=language_probe_repo,
            lsp_metrics_provider=shared_hub.get_metrics,
            reconcile_state_provider=admin_service.get_runtime_reconcile_state,
            pipeline_control_service=pipeline_control_service,
        )
        self._read_tool = ReadTool(
            workspace_repo=workspace_repo,
            file_collection_service=file_collection_service,
            lsp_repo=lsp_repo,
            knowledge_repo=knowledge_repo,
            tool_layer_repo=tool_layer_repo,
            stabilization_service=stabilization_service,
        )
        self._dry_run_diff_tool = DryRunDiffTool(read_tool=self._read_tool)
        self._list_symbols_tool = ListSymbolsTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._read_symbol_tool = ReadSymbolTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._get_implementations_tool = GetImplementationsTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._call_graph_tool = CallGraphTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._call_graph_health_tool = CallGraphHealthTool(workspace_repo=workspace_repo, lsp_repo=lsp_repo)
        self._knowledge_tool = KnowledgeTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._save_snippet_tool = SaveSnippetTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._get_snippet_tool = GetSnippetTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._archive_context_tool = ArchiveContextTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._get_context_tool = GetContextTool(workspace_repo=workspace_repo, knowledge_repo=knowledge_repo)
        self._pipeline_policy_get_tool = PipelinePolicyGetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_policy_set_tool = PipelinePolicySetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_alert_status_tool = PipelineAlertStatusTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_list_tool = PipelineDeadListTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_requeue_tool = PipelineDeadRequeueTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_dead_purge_tool = PipelineDeadPurgeTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_status_tool = PipelineAutoStatusTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_set_tool = PipelineAutoSetTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_auto_tick_tool = PipelineAutoTickTool(workspace_repo=workspace_repo, service=pipeline_control_service)
        self._pipeline_perf_run_tool = PipelinePerfRunTool(workspace_repo=workspace_repo, perf_service=perf_service)
        self._pipeline_perf_report_tool = PipelinePerfReportTool(workspace_repo=workspace_repo, perf_service=perf_service)
        self._pipeline_quality_run_tool = PipelineQualityRunTool(workspace_repo=workspace_repo, quality_service=quality_service)
        self._pipeline_quality_report_tool = PipelineQualityReportTool(workspace_repo=workspace_repo, quality_service=quality_service)
        self._pipeline_lsp_matrix_run_tool = PipelineLspMatrixRunTool(workspace_repo=workspace_repo, matrix_service=pipeline_lsp_matrix_service)
        self._pipeline_lsp_matrix_report_tool = PipelineLspMatrixReportTool(workspace_repo=workspace_repo, matrix_service=pipeline_lsp_matrix_service)
        self._tool_handler_attrs: dict[str, str] = build_tool_handler_attr_map()

    def close(self) -> None:
        """MCP 서버가 생성한 런타임 리소스를 명시적으로 종료한다."""
        if self._closed:
            return
        self._closed = True
        stop_errors: list[str] = []
        for service in (self._file_collection_service,):
            try:
                service.stop_background()
            except (RuntimeError, OSError, ValueError) as exc:
                stop_errors.append(str(exc))
        for hub in self._managed_lsp_hubs:
            try:
                hub.stop_all()
            except DaemonError as exc:
                stop_errors.append(exc.context.code)
        if len(stop_errors) > 0:
            raise DaemonError(
                ErrorContext(
                    code="ERR_MCP_CLOSE_FAILED",
                    message=f"MCP close 중 오류 {len(stop_errors)}건: {stop_errors[0]}",
                )
            )

    def handle_request(self, payload: dict[str, object]) -> McpResponse:
        request_id = payload.get('id')
        method = payload.get('method')
        if not isinstance(method, str):
            return McpResponse(request_id=request_id, result=None, error=McpError(code=-32600, message='invalid request'))
        if self._should_forward(payload, method):
            return self._forward_to_daemon(payload=payload, request_id=request_id)
        if method == 'initialize':
            params = payload.get('params')
            if not isinstance(params, dict):
                params = {}
            try:
                protocol_version = self._negotiate_protocol_version(params=params)
            except ValueError as exc:
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message=str(exc)))
            return McpResponse(
                request_id=request_id,
                result={
                    'protocolVersion': protocol_version,
                    'serverInfo': {'name': 'sari-v2', 'version': SARI_VERSION},
                    'schemaVersion': self._TOOLS_SCHEMA_VERSION,
                    'schema_version': self._TOOLS_SCHEMA_VERSION,
                    'capabilities': {'tools': {}},
                },
                error=None,
            )
        if method == 'sari/identify':
            return McpResponse(
                request_id=request_id,
                result={
                    'name': 'sari-v2',
                    'version': SARI_VERSION,
                    'schemaVersion': self._TOOLS_SCHEMA_VERSION,
                    'schema_version': self._TOOLS_SCHEMA_VERSION,
                    'workspaceRoot': self._default_workspace_root(),
                    'pid': os.getpid(),
                },
                error=None,
            )
        if method == 'prompts/list':
            return McpResponse(request_id=request_id, result={'prompts': []}, error=None)
        if method == 'resources/list':
            return McpResponse(request_id=request_id, result={'resources': []}, error=None)
        if method == 'resources/templates/list':
            return McpResponse(request_id=request_id, result={'resourceTemplates': []}, error=None)
        if method == 'roots/list':
            return McpResponse(request_id=request_id, result={'roots': self._roots_list()}, error=None)
        if method == 'initialized':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'notifications/initialized':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'ping':
            return McpResponse(request_id=request_id, result={}, error=None)
        if method == 'tools/list':
            result_payload = build_tools_list_result_payload(self._TOOLS_SCHEMA_VERSION)
            response_payload = {"result": result_payload}
            decorated_payload = filter_tools_list_response_payload(response_payload)
            decorated_result = decorated_payload.get("result")
            if isinstance(decorated_result, dict):
                result_payload = decorated_result
            return McpResponse(request_id=request_id, result=result_payload, error=None)
        if method == 'tools/call':
            params = payload.get('params')
            if not isinstance(params, dict):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message='invalid params'))
            tool_name = params.get('name')
            if is_hidden_tool_name(tool_name):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='tool not found'))
            arguments = params.get('arguments', {})
            if not isinstance(arguments, dict):
                return McpResponse(request_id=request_id, result=None, error=McpError(code=-32602, message='invalid arguments'))
            try:
                normalized = normalize_tool_arguments(str(tool_name), arguments)
                arguments = normalized.arguments
                handler_attr = self._tool_handler_attrs.get(str(tool_name))
                handler = getattr(self, handler_attr) if isinstance(handler_attr, str) and hasattr(self, handler_attr) else None
                if handler is not None and hasattr(handler, "call"):
                    raw_result = handler.call(arguments)
                    if isinstance(raw_result, dict):
                        pack_result = self._render_pack_v2(
                            tool_name=str(tool_name),
                            arguments=arguments,
                            payload=raw_result,
                        )
                        return McpResponse(request_id=request_id, result=pack_result, error=None)
                    return McpResponse(request_id=request_id, result=raw_result, error=None)
            except ValidationError as exc:
                payload = pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
                pack_result = self._render_pack_v2(
                    tool_name=str(tool_name) if isinstance(tool_name, str) else "unknown",
                    arguments=arguments,
                    payload=payload,
                )
                return McpResponse(request_id=request_id, result=pack_result, error=None)
            except ArgNormalizationError as exc:
                payload = pack1_error(
                    error=exc.to_error_dto(),
                    expected=exc.hint.expected,
                    received=exc.hint.received,
                    example=exc.hint.example,
                    normalized_from=exc.hint.normalized_from,
                )
                pack_result = self._render_pack_v2(
                    tool_name=str(tool_name) if isinstance(tool_name, str) else "unknown",
                    arguments=arguments,
                    payload=payload,
                )
                return McpResponse(request_id=request_id, result=pack_result, error=None)
            return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='tool not found'))
        return McpResponse(request_id=request_id, result=None, error=McpError(code=-32601, message='method not found'))

    def _should_forward(self, payload: dict[str, object], method: str) -> bool:
        _ = payload
        return self._daemon_router.should_forward(method=method)

    def _forward_to_daemon(self, payload: dict[str, object], request_id: object) -> McpResponse:
        self._daemon_router = DaemonRouter(
            db_path=self._db_path,
            config=DaemonRouterConfig(
                proxy_to_daemon=self._proxy_to_daemon,
                auto_start_on_failure=self._daemon_autostart_on_failure,
                timeout_sec=self._daemon_forward_timeout_sec,
            ),
            start_daemon_fn=self._daemon_start_fn,
            resolve_target_fn=resolve_target,
            forward_once_fn=forward_once,
        )
        response = self._daemon_router.forward(payload=payload, request_id=request_id)
        if response.error is not None:
            return response
        result_payload = response.result
        if str(payload.get("method", "")).strip() == "tools/list" and isinstance(result_payload, dict):
            if "schemaVersion" not in result_payload:
                result_payload["schemaVersion"] = self._TOOLS_SCHEMA_VERSION
            if "schema_version" not in result_payload:
                result_payload["schema_version"] = self._TOOLS_SCHEMA_VERSION
            decorated_payload = filter_tools_list_response_payload({"result": result_payload})
            decorated_result = decorated_payload.get("result")
            if isinstance(decorated_result, dict):
                result_payload = decorated_result
        return McpResponse(request_id=response.request_id, result=result_payload, error=None)

    def _roots_list(self) -> list[dict[str, str]]:
        roots: list[dict[str, str]] = []
        for workspace in self._workspace_repo.list_all():
            root_path = workspace.path
            name = Path(root_path).name if Path(root_path).name != '' else root_path
            roots.append({'uri': f'file://{root_path}', 'name': name})
        return roots

    def _default_workspace_root(self) -> str:
        workspaces = self._workspace_repo.list_all()
        if len(workspaces) == 0:
            return ''
        return workspaces[0].path

    def _negotiate_protocol_version(self, params: dict[str, object]) -> str:
        versions = self._iter_client_protocol_versions(params=params)
        for candidate in versions:
            if candidate in self._SUPPORTED_PROTOCOL_VERSIONS:
                return candidate
        return self._DEFAULT_PROTOCOL_VERSION

    def _iter_client_protocol_versions(self, params: dict[str, object]) -> list[str]:
        versions: list[str] = []
        seen: set[str] = set()

        def _append(raw_value: object) -> None:
            if not isinstance(raw_value, str):
                return
            normalized = raw_value.strip()
            if normalized == '' or normalized in seen:
                return
            seen.add(normalized)
            versions.append(normalized)

        _append(params.get('protocolVersion'))
        supported = params.get('supportedProtocolVersions')
        if isinstance(supported, list):
            for item in supported:
                _append(item)
        capabilities = params.get('capabilities')
        if isinstance(capabilities, dict):
            cap_versions = capabilities.get('protocolVersions')
            if isinstance(cap_versions, list):
                for item in cap_versions:
                    _append(item)
        return versions

    def _render_pack_v2(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]:
        """도구 payload를 PACK1 v2 라인 포맷으로 렌더링한다."""
        include_structured = _is_structured_requested(arguments=arguments)
        return render_pack_v2(
            tool_name=tool_name,
            arguments=arguments,
            payload=payload,
            options=PackLineOptionsDTO(
                include_structured=include_structured,
                include_score=_is_score_requested(arguments=arguments),
            ),
        )


def _is_structured_requested(arguments: dict[str, object]) -> bool:
    """options.structured=1 요청 여부를 판정한다."""
    options = arguments.get("options")
    if isinstance(options, dict):
        raw_structured = options.get("structured")
        if isinstance(raw_structured, bool):
            return raw_structured
        if isinstance(raw_structured, int):
            return raw_structured == 1
        if isinstance(raw_structured, str):
            return raw_structured.strip().lower() in {"1", "true", "yes", "on"}
    raw_structured = arguments.get("structured")
    if isinstance(raw_structured, bool):
        return raw_structured
    if isinstance(raw_structured, int):
        return raw_structured == 1
    if isinstance(raw_structured, str):
        return raw_structured.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _is_score_requested(arguments: dict[str, object]) -> bool:
    """options.include_score=1 요청 여부를 판정한다."""
    options = arguments.get("options")
    if isinstance(options, dict):
        raw_score = options.get("include_score")
        if isinstance(raw_score, bool):
            return raw_score
        if isinstance(raw_score, int):
            return raw_score == 1
        if isinstance(raw_score, str):
            return raw_score.strip().lower() in {"1", "true", "yes", "on"}
    raw_score = arguments.get("include_score")
    if isinstance(raw_score, bool):
        return raw_score
    if isinstance(raw_score, int):
        return raw_score == 1
    if isinstance(raw_score, str):
        return raw_score.strip().lower() in {"1", "true", "yes", "on"}
    return False

def run_stdio_streams(db_path: Path, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    server = McpServer(db_path=db_path)
    runtime = server._runtime_repo.get_runtime()
    if runtime is not None:
        server._runtime_repo.increment_session()
    transport = McpTransport(input_stream=input_stream, output_stream=output_stream, allow_jsonl=True)
    transport.default_mode = MCP_MODE_FRAMED
    consecutive_invalid_frames = 0
    try:
        while True:
            try:
                read_result = transport.read_message()
            except McpTransportParseError as exc:
                consecutive_invalid_frames += 1
                parse_response = McpResponse(request_id=None, result=None, error=McpError(code=-32700, message=str(exc)))
                transport.write_message(parse_response.to_dict(), mode=exc.mode)
                if (not exc.recoverable) or consecutive_invalid_frames > MAX_CONSECUTIVE_INVALID_FRAMES:
                    return 0
                if not transport.drain_for_resync(mode=exc.mode):
                    return 0
                continue
            if read_result is None:
                return 0
            consecutive_invalid_frames = 0
            payload, mode = read_result
            response = server.handle_request(payload)
            response_payload = response.to_dict()
            if str(payload.get("method", "")).strip() == "tools/list":
                response_payload = filter_tools_list_response_payload(response_payload)
            transport.write_message(response_payload, mode=mode)
    finally:
        runtime = server._runtime_repo.get_runtime()
        if runtime is not None:
            server._runtime_repo.decrement_session()
        server.close()
    return 0

def run_stdio(db_path: Path) -> int:
    input_stream = getattr(sys.stdin, 'buffer', sys.stdin)
    output_stream = getattr(sys.stdout, 'buffer', sys.stdout)
    return run_stdio_streams(db_path=db_path, input_stream=input_stream, output_stream=output_stream)

def _parse_daemon_forward_timeout(raw_value: str) -> float:
    if raw_value == '':
        return 2.0
    try:
        parsed = float(raw_value)
    except ValueError:
        return 2.0
    if parsed <= 0:
        return 2.0
    return parsed
