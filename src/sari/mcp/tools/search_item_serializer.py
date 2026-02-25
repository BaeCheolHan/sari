"""search item payload 직렬화 로직."""

from __future__ import annotations

from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.mcp.tools.admin_tools import RepoValidationPort


class SearchItemSerializer:
    """SearchItemDTO -> pack1 item dict 직렬화."""

    def __init__(
        self,
        *,
        workspace_repo: RepoValidationPort | None,
        tool_layer_repo: ToolDataLayerRepository | None,
    ) -> None:
        self._workspace_repo = workspace_repo
        self._tool_layer_repo = tool_layer_repo

    def serialize(self, *, item: object, repo_root: str) -> dict[str, object]:
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
        tool_layer_repo = self._tool_layer_repo
        workspace_repo = self._workspace_repo
        content_hash = getattr(item, "content_hash", None)
        if (
            tool_layer_repo is None
            or workspace_repo is None
            or not isinstance(content_hash, str)
            or content_hash.strip() == ""
        ):
            return payload
        relative_path = getattr(item, "relative_path", None)
        if not isinstance(relative_path, str) or relative_path.strip() == "":
            return payload
        workspace = workspace_repo.get_by_path(repo_root)
        if workspace is None:
            return payload
        effective_repo_root = getattr(item, "repo", repo_root)
        if not isinstance(effective_repo_root, str) or effective_repo_root.strip() == "":
            effective_repo_root = repo_root
        snapshot = tool_layer_repo.load_effective_snapshot(
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
        # NOTE(policy): External tool output must expose exactly one canonical line.
        # We prefer L3(AST/text) coordinates for safe edits. L5/LSP semantic lines stay internal.
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

