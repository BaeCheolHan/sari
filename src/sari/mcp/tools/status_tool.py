"""status MCP 도구 구현."""

from __future__ import annotations

from typing import Callable

from sari.core.language.registry import get_enabled_language_names
from sari.core.models import LanguageProbeStatusDTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.mcp.tools.admin_tools import RepoValidationPort, validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.pipeline.control_service import PipelineControlService


def _success(items: list[dict[str, object]], *, warnings: list[dict[str, object]] | None = None) -> dict[str, object]:
    """pack1 success 응답을 생성한다."""
    return pack1_success(
        {
            "items": items,
            "meta": Pack1MetaDTO(
                candidate_count=len(items),
                resolved_count=len(items),
                cache_hit=False,
                errors=[],
                stabilization=None,
                warnings=warnings,
            ).to_dict(),
        }
    )


def _build_language_support_payload(language_probe_repo: LanguageProbeRepository | None) -> dict[str, object]:
    """언어 지원 상태 페이로드를 구성한다."""
    enabled_languages = list(get_enabled_language_names())
    snapshots: dict[str, LanguageProbeStatusDTO] = {}
    if language_probe_repo is not None:
        for item in language_probe_repo.list_all():
            snapshots[item.language] = item
    languages: list[dict[str, object]] = []
    for language in enabled_languages:
        snapshot = snapshots.get(language)
        if snapshot is None:
            languages.append(
                {
                    "language": language,
                    "enabled": True,
                    "available": False,
                    "last_probe_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                    "symbol_extract_success": False,
                    "document_symbol_count": 0,
                    "path_mapping_ok": False,
                    "timeout_occurred": False,
                    "recovered_by_restart": False,
                }
            )
            continue
        languages.append(
            {
                "language": snapshot.language,
                "enabled": snapshot.enabled,
                "available": snapshot.available,
                "last_probe_at": snapshot.last_probe_at,
                "last_error_code": snapshot.last_error_code,
                "last_error_message": snapshot.last_error_message,
                "symbol_extract_success": snapshot.symbol_extract_success,
                "document_symbol_count": snapshot.document_symbol_count,
                "path_mapping_ok": snapshot.path_mapping_ok,
                "timeout_occurred": snapshot.timeout_occurred,
                "recovered_by_restart": snapshot.recovered_by_restart,
            }
        )
    available_count = len([item for item in languages if bool(item["available"])])
    return {
        "enabled": enabled_languages,
        "enabled_count": len(enabled_languages),
        "available_count": available_count,
        "active_last_5m": [],
        "languages": languages,
    }


class StatusTool:
    """status MCP 도구를 처리한다."""

    def __init__(
        self,
        workspace_repo: RepoValidationPort,
        runtime_repo: RuntimeRepository,
        file_repo: FileCollectionRepository,
        lsp_repo: LspToolDataRepository,
        language_probe_repo: LanguageProbeRepository | None = None,
        lsp_metrics_provider: Callable[[], dict[str, int]] | None = None,
        reconcile_state_provider: Callable[[], dict[str, object]] | None = None,
        pipeline_control_service: PipelineControlService | None = None,
        mcp_startup_provider: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._runtime_repo = runtime_repo
        self._file_repo = file_repo
        self._lsp_repo = lsp_repo
        self._language_probe_repo = language_probe_repo
        self._lsp_metrics_provider = lsp_metrics_provider
        self._reconcile_state_provider = reconcile_state_provider
        self._pipeline_control_service = pipeline_control_service
        self._mcp_startup_provider = mcp_startup_provider

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 단위 상태 요약을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo_root = str(arguments["repo"])
        runtime = self._runtime_repo.get_runtime()
        file_count = self._file_repo.count_active_files_by_scope(scope_repo_root=repo_root)
        module_repo_count = self._file_repo.count_distinct_repo_roots_by_scope(scope_repo_root=repo_root)
        if file_count == 0:
            # fanout 이전/혼합 데이터에서는 module row가 repo_root 기준으로만 존재할 수 있다.
            # list_files/read_file과 동일한 하위호환 계약을 위해 repo_root 카운트로 폴백한다.
            repo_file_count = self._file_repo.count_active_files(repo_root=repo_root)
            if repo_file_count > 0:
                file_count = repo_file_count
                module_repo_count = 1
        repo_scope_kind = "workspace_scope" if module_repo_count > 1 else "module_scope"
        graph_health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        language_support = _build_language_support_payload(self._language_probe_repo)
        lsp_metrics: dict[str, int] = {}
        if self._lsp_metrics_provider is not None:
            raw_metrics = self._lsp_metrics_provider()
            if isinstance(raw_metrics, dict):
                for key, value in raw_metrics.items():
                    lsp_metrics[str(key)] = int(value)
        reconcile_state: dict[str, object] = {
            "reconcile_last_run_ts": None,
            "reconcile_last_result": None,
            "reconcile_last_error_code": None,
            "reconcile_last_error_message": None,
        }
        if self._reconcile_state_provider is not None:
            raw_state = self._reconcile_state_provider()
            if isinstance(raw_state, dict):
                reconcile_state = dict(raw_state)
                reconcile_state.setdefault("reconcile_last_run_ts", None)
                reconcile_state.setdefault("reconcile_last_result", None)
                reconcile_state.setdefault("reconcile_last_error_code", None)
                reconcile_state.setdefault("reconcile_last_error_message", None)
        auto_control: dict[str, object] | None = None
        stage_rollout: dict[str, object] | None = None
        if self._pipeline_control_service is not None:
            auto_control = self._pipeline_control_service.get_auto_control_state().to_dict()
            stage_rollout = self._pipeline_control_service.get_stage_rollout_state()
        mcp_startup: dict[str, object] | None = None
        if self._mcp_startup_provider is not None:
            raw_startup = self._mcp_startup_provider()
            if isinstance(raw_startup, dict):
                mcp_startup = dict(raw_startup)
        return _success(
            [
                {
                    "repo": repo_root,
                    "scope_repo_root": repo_root,
                    "repo_scope_kind": repo_scope_kind,
                    "module_repo_count": module_repo_count,
                    "daemon_state": None if runtime is None else runtime.state,
                    "file_count": file_count,
                    "symbol_count": graph_health["symbol_count"],
                    "relation_count": graph_health["relation_count"],
                    "orphan_relation_count": graph_health["orphan_relation_count"],
                    "language_support": language_support,
                    "lsp_metrics": lsp_metrics,
                    "reconcile_state": reconcile_state,
                    "auto_control": auto_control,
                    "stage_rollout": stage_rollout,
                    "mcp_startup": mcp_startup,
                }
            ],
            warnings=warnings_payload,
        )
