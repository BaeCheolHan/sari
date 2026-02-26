"""HTTP 응답 조립 전용 프레젠테이션 서비스."""

from __future__ import annotations

from sari.core.language.registry import get_enabled_language_names
from sari.core.models import LanguageProbeStatusDTO
from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository


class HttpPresentationService:
    """HTTP 계층 응답 payload 조립을 담당한다."""

    def __init__(
        self,
        *,
        workspace_repo: WorkspaceRepository,
        language_probe_repo: LanguageProbeRepository | None = None,
        tool_layer_repo: ToolDataLayerRepository | None = None,
    ) -> None:
        self._workspace_repo = workspace_repo
        self._language_probe_repo = language_probe_repo
        self._tool_layer_repo = tool_layer_repo

    @property
    def supports_tool_layer_snapshot(self) -> bool:
        """L4/L5 snapshot 병합 가능 여부를 반환한다."""
        return self._tool_layer_repo is not None

    def build_language_support_payload(self) -> dict[str, object]:
        """status 응답용 language_support payload를 생성한다."""
        enabled_languages = list(get_enabled_language_names())
        snapshot_by_language: dict[str, LanguageProbeStatusDTO] = {}
        if self._language_probe_repo is not None:
            for item in self._language_probe_repo.list_all():
                snapshot_by_language[item.language] = item
        languages: list[dict[str, object]] = []
        for language in enabled_languages:
            snapshot = snapshot_by_language.get(language)
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

    def build_search_item_payload(self, *, repo_root: str, item: object) -> dict[str, object]:
        """search 응답 item payload를 생성한다."""
        payload: dict[str, object] = {
            "type": getattr(item, "item_type"),
            "repo": getattr(item, "repo"),
            "relative_path": getattr(item, "relative_path"),
            "score": getattr(item, "score"),
            "source": getattr(item, "source"),
            "name": getattr(item, "name"),
            "kind": getattr(item, "kind"),
            "symbol_info": getattr(item, "symbol_info"),
        }
        if self._tool_layer_repo is None:
            return payload
        content_hash = getattr(item, "content_hash", None)
        relative_path = getattr(item, "relative_path", None)
        if not isinstance(content_hash, str) or content_hash.strip() == "":
            return payload
        if not isinstance(relative_path, str) or relative_path.strip() == "":
            return payload
        workspace = self._workspace_repo.get_by_path(repo_root)
        if workspace is None:
            return payload
        effective_repo_root = getattr(item, "repo", repo_root)
        if not isinstance(effective_repo_root, str) or effective_repo_root.strip() == "":
            effective_repo_root = repo_root
        snapshot = self._tool_layer_repo.load_effective_snapshot(
            workspace_id=workspace.path,
            repo_root=effective_repo_root,
            relative_path=relative_path,
            content_hash=content_hash,
        )
        l4_snapshot = snapshot.get("l4")
        if isinstance(l4_snapshot, dict):
            payload["l4"] = l4_snapshot
        l5_snapshot = snapshot.get("l5", [])
        if isinstance(l5_snapshot, list) and len(l5_snapshot) > 0:
            payload["l5"] = l5_snapshot
        self._attach_single_line_policy(payload=payload, item=item, snapshot=snapshot)
        return payload

    def _attach_single_line_policy(self, *, payload: dict[str, object], item: object, snapshot: dict[str, object]) -> None:
        # NOTE(policy): External API must expose a single canonical line only.
        # We intentionally prefer L3(AST/text) coordinates for editing safety across languages.
        # L5/LSP semantic coordinates are internal hints and must not be exposed as a second line.
        if str(payload.get("type", "")) != "symbol":
            return
        line, end_line = self._resolve_canonical_line(item=item, snapshot=snapshot)
        if line is None:
            return
        payload["line"] = int(line)
        payload["end_line"] = int(end_line if end_line is not None else line)

    def _resolve_canonical_line(self, *, item: object, snapshot: dict[str, object]) -> tuple[int | None, int | None]:
        l3 = snapshot.get("l3")
        name = getattr(item, "name", None)
        kind = getattr(item, "kind", None)
        if isinstance(l3, dict):
            symbols = l3.get("symbols")
            if isinstance(symbols, list):
                for symbol in symbols:
                    if not isinstance(symbol, dict):
                        continue
                    symbol_name = symbol.get("name")
                    symbol_kind = symbol.get("kind")
                    if isinstance(name, str) and name.strip() != "" and str(symbol_name) != name:
                        continue
                    if isinstance(kind, str) and kind.strip() != "" and str(symbol_kind) != kind:
                        continue
                    try:
                        line = int(symbol.get("line", 0))
                        end_line = int(symbol.get("end_line", line))
                    except (TypeError, ValueError):
                        continue
                    if line > 0:
                        return (line, end_line if end_line >= line else line)
        raw_line = getattr(item, "line", None)
        raw_end_line = getattr(item, "end_line", None)
        try:
            if raw_line is not None:
                line = int(raw_line)
                if line > 0:
                    end_line = int(raw_end_line) if raw_end_line is not None else line
                    return (line, end_line if end_line >= line else line)
        except (TypeError, ValueError):
            return (None, None)
        return (None, None)
