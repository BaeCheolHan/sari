"""status MCP 도구 구현."""

from __future__ import annotations

from sari.core.language_registry import get_enabled_language_names
from sari.core.models import LanguageProbeStatusDTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success


def _success(items: list[dict[str, object]]) -> dict[str, object]:
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
        workspace_repo: WorkspaceRepository,
        runtime_repo: RuntimeRepository,
        file_repo: FileCollectionRepository,
        lsp_repo: LspToolDataRepository,
        language_probe_repo: LanguageProbeRepository | None = None,
    ) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._runtime_repo = runtime_repo
        self._file_repo = file_repo
        self._lsp_repo = lsp_repo
        self._language_probe_repo = language_probe_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 단위 상태 요약을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        repo_root = str(arguments["repo"])
        runtime = self._runtime_repo.get_runtime()
        repo_stats = self._file_repo.get_repo_stats()
        file_count = 0
        for stat in repo_stats:
            if str(stat.get("repo", "")) == repo_root:
                file_count = int(stat.get("file_count", 0))
                break
        graph_health = self._lsp_repo.get_repo_call_graph_health(repo_root=repo_root)
        language_support = _build_language_support_payload(self._language_probe_repo)
        return _success(
            [
                {
                    "repo": repo_root,
                    "daemon_state": None if runtime is None else runtime.state,
                    "file_count": file_count,
                    "symbol_count": graph_health["symbol_count"],
                    "relation_count": graph_health["relation_count"],
                    "orphan_relation_count": graph_health["orphan_relation_count"],
                    "language_support": language_support,
                }
            ]
        )
