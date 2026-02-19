"""read/dry_run_diff MCP 도구 구현."""

from __future__ import annotations

from pathlib import Path

from sari.core.exceptions import CollectionError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.stabilization.aggregation import add_read_to_bundle
from sari.mcp.stabilization.budget_guard import apply_soft_limits, evaluate_budget_state
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.relevance_guard import assess_relevance
from sari.mcp.stabilization.session_state import (
    get_metrics_snapshot,
    get_search_context,
    get_session_key,
    record_read_metrics,
    requires_strict_session_id,
)
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.tool_common import (
    argument_error,
    content_hash,
    normalize_source_path,
    pack1_items_success,
    resolve_source_path,
)
from sari.services.collection.ports import CollectionScanPort


class ReadTool:
    """read MCP unified 도구를 처리한다."""

    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        file_collection_service: CollectionScanPort,
        lsp_repo: LspToolDataRepository,
        knowledge_repo: KnowledgeRepository,
        stabilization_enabled: bool = True,
    ) -> None:
        """필요 저장소/서비스 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._file_collection_service = file_collection_service
        self._lsp_repo = lsp_repo
        self._knowledge_repo = knowledge_repo
        self._stabilization_enabled = stabilization_enabled

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """모드별 read 응답을 반환한다."""
        error = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if error is not None:
            return pack1_error(error)
        stabilization_enabled = self._stabilization_enabled
        if stabilization_enabled and requires_strict_session_id(arguments):
            return pack1_error(
                ErrorResponseDTO(code="ERR_SESSION_ID_REQUIRED", message="session_id is required by strict session policy."),
                stabilization={
                    "budget_state": "NORMAL",
                    "suggested_next_action": "read",
                    "warnings": ["Provide session_id or disable strict mode."],
                    "reason_codes": [ReasonCode.SESSION_ID_REQUIRED.value],
                    "next_calls": [],
                    "metrics_snapshot": get_metrics_snapshot(arguments, [str(arguments["repo"])]),
                },
            )
        repo_root = str(arguments["repo"])
        if stabilization_enabled:
            pre_metrics = get_metrics_snapshot(arguments, [repo_root])
            budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(pre_metrics)
            if budget_state == "HARD_LIMIT":
                return pack1_error(
                    ErrorResponseDTO(code="ERR_BUDGET_HARD_LIMIT", message="read budget exceeded. use search first."),
                    stabilization={
                        "budget_state": budget_state,
                        "suggested_next_action": suggested_next_action or "search",
                        "warnings": budget_warnings,
                        "reason_codes": [ReasonCode.BUDGET_HARD_LIMIT.value],
                        "next_calls": [{"tool": "search", "arguments": {"query": "target", "limit": 5}}],
                        "metrics_snapshot": pre_metrics,
                    },
                )
        mode_raw = arguments.get("mode", "file")
        if not isinstance(mode_raw, str) or mode_raw.strip() == "":
            return argument_error(
                code="ERR_MODE_REQUIRED",
                message="mode is required",
                arguments=arguments,
                expected=["mode"],
                example={"repo": repo_root, "mode": "file", "target": "README.md"},
            )
        mode = mode_raw.strip().lower()
        if mode == "ast_edit":
            return pack1_error(ErrorResponseDTO(code="ERR_AST_DISABLED", message="ast_edit mode is disabled by policy"))
        if mode == "file":
            return self._read_file_mode(repo_root=repo_root, arguments=arguments)
        if mode == "symbol":
            return self._read_symbol_mode(repo_root=repo_root, arguments=arguments)
        if mode == "snippet":
            return self._read_snippet_mode(repo_root=repo_root, arguments=arguments)
        if mode == "diff_preview":
            return self._read_diff_preview_mode(repo_root=repo_root, arguments=arguments)
        return argument_error(
            code="ERR_UNSUPPORTED_MODE",
            message=f"unsupported mode: {mode}",
            arguments=arguments,
            expected=["file", "symbol", "snippet", "diff_preview"],
            example={"repo": repo_root, "mode": "file", "target": "README.md"},
        )

    def _build_stabilization_meta(
        self,
        arguments: dict[str, object],
        repo_root: str,
        mode: str,
        target: str,
        content_text: str,
        read_lines: int,
        read_span: int,
        warnings: list[str],
        degraded: bool,
    ) -> dict[str, object] | None:
        """read 성공 응답용 stabilization 메타를 생성한다."""
        if not self._stabilization_enabled:
            return None
        metrics_snapshot = record_read_metrics(
            arguments,
            [repo_root],
            read_lines=read_lines,
            read_chars=len(content_text),
            read_span=read_span,
        )
        budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(metrics_snapshot)
        search_context = get_search_context(arguments, [repo_root])
        relevance_state, relevance_warnings, relevance_alternatives, relevance_suggested = assess_relevance(
            mode=mode,
            target=target,
            search_context=search_context,
        )
        session_key = get_session_key(arguments, [repo_root])
        bundle_info = add_read_to_bundle(
            session_key=session_key,
            mode=mode,
            path=target,
            text=content_text,
        )
        reason_codes: list[str] = []
        if degraded:
            reason_codes.append(ReasonCode.BUDGET_SOFT_LIMIT.value)
        if relevance_state == "LOW_RELEVANCE":
            reason_codes.append(ReasonCode.LOW_RELEVANCE_OUTSIDE_TOPK.value)
        all_warnings = [*warnings, *budget_warnings, *relevance_warnings]
        next_calls = self._next_calls_for_read(mode=mode, target=target, alternatives=relevance_alternatives)
        suggested = relevance_suggested or suggested_next_action or "read"
        return {
            "budget_state": budget_state,
            "suggested_next_action": suggested,
            "warnings": all_warnings,
            "reason_codes": reason_codes,
            "bundle_id": str(bundle_info.get("context_bundle_id") or ""),
            "next_calls": next_calls,
            "metrics_snapshot": metrics_snapshot,
            "evidence_refs": [
                {
                    "kind": mode,
                    "path": target,
                    "content_hash": content_hash(content_text),
                }
            ],
        }

    def _next_calls_for_read(self, mode: str, target: str, alternatives: list[str]) -> list[dict[str, object]]:
        """read 응답의 다음 호출 힌트를 생성한다."""
        if mode == "symbol":
            return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]
        if len(alternatives) > 0:
            return [{"tool": "read", "arguments": {"mode": "file", "target": alternatives[0]}}]
        return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]

    def _read_file_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """file 모드 읽기를 수행한다."""
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return argument_error(
                code="ERR_TARGET_REQUIRED",
                message="target is required",
                arguments=arguments,
                expected=["target"],
                example={"repo": repo_root, "mode": "file", "target": "README.md"},
            )
        offset_raw = arguments.get("offset", 0)
        limit_raw = arguments.get("limit", 300)
        if not isinstance(offset_raw, int) or offset_raw < 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_OFFSET", message="offset must be non-negative integer"))
        if limit_raw is not None and (not isinstance(limit_raw, int) or limit_raw <= 0):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer or null"))
        delegated_args = {"offset": offset_raw, "limit": limit_raw}
        constrained_args, degraded, soft_warnings = apply_soft_limits(mode="file", delegated_args=delegated_args)
        constrained_offset = constrained_args.get("offset", offset_raw)
        constrained_limit = constrained_args.get("limit", limit_raw)
        if not isinstance(constrained_offset, int) or constrained_offset < 0:
            constrained_offset = offset_raw
        if constrained_limit is not None and not isinstance(constrained_limit, int):
            constrained_limit = limit_raw
        try:
            result = self._file_collection_service.read_file(
                repo_root=repo_root,
                relative_path=target.strip(),
                offset=constrained_offset,
                limit=constrained_limit if isinstance(constrained_limit, int) else None,
            )
        except CollectionError as exc:
            return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="file",
            target=result.relative_path,
            content_text=result.content,
            read_lines=max(0, result.end_line - result.start_line + 1),
            read_span=max(0, result.end_line - result.start_line + 1),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return pack1_items_success(
            [
                {
                    "relative_path": result.relative_path,
                    "content": result.content,
                    "start_line": result.start_line,
                    "end_line": result.end_line,
                    "source": result.source,
                    "total_lines": result.total_lines,
                    "is_truncated": result.is_truncated,
                    "next_offset": result.next_offset,
                }
            ],
            cache_hit=result.source == "l2",
            stabilization=stabilization,
        )

    def _read_symbol_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """symbol 모드 읽기를 수행한다."""
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        path_raw = arguments.get("path")
        path_prefix = path_raw if isinstance(path_raw, str) and path_raw.strip() != "" else None
        rows = self._lsp_repo.search_symbols(repo_root=repo_root, query=target.strip(), limit=limit_raw, path_prefix=path_prefix)
        row_items = [row.to_dict() for row in rows]
        content_text = "\n".join(str(item.get("name", "")) for item in row_items)
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="symbol",
            target=target.strip(),
            content_text=content_text,
            read_lines=len(row_items),
            read_span=len(row_items),
            warnings=[],
            degraded=False,
        )
        return pack1_items_success(row_items, cache_hit=True, stabilization=stabilization)

    def _read_snippet_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """snippet 모드 읽기를 수행한다."""
        target = arguments.get("target")
        query = None if not isinstance(target, str) else target.strip()
        if query is not None and query == "":
            query = None
        tag_raw = arguments.get("tag")
        tag = None if not isinstance(tag_raw, str) else tag_raw.strip()
        if tag == "":
            tag = None
        if tag is None and query is None:
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target or tag is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        constrained_args, degraded, soft_warnings = apply_soft_limits(
            mode="snippet",
            delegated_args={"limit": limit_raw, "context_lines": arguments.get("context_lines")},
        )
        constrained_limit = constrained_args.get("limit", limit_raw)
        if not isinstance(constrained_limit, int) or constrained_limit <= 0:
            constrained_limit = limit_raw
        rows = self._knowledge_repo.query_snippets(repo_root=repo_root, tag=tag, query=query, limit=constrained_limit)
        row_items = [row.to_dict() for row in rows]
        content_text = "\n".join(str(item.get("content", "")) for item in row_items)
        target_value = query if query is not None else (tag if tag is not None else "")
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="snippet",
            target=target_value,
            content_text=content_text,
            read_lines=len(row_items),
            read_span=len(row_items),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return pack1_items_success(row_items, cache_hit=True, stabilization=stabilization)

    def _read_diff_preview_mode(self, repo_root: str, arguments: dict[str, object]) -> dict[str, object]:
        """diff_preview 모드 읽기를 수행한다."""
        target = arguments.get("target")
        content = arguments.get("content")
        if not isinstance(target, str) or target.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        if not isinstance(content, str):
            return pack1_error(ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required"))
        against_raw = arguments.get("against", "WORKTREE")
        against = against_raw if isinstance(against_raw, str) and against_raw.strip() != "" else "WORKTREE"
        constrained_args, degraded, soft_warnings = apply_soft_limits(
            mode="diff_preview",
            delegated_args={"max_preview_chars": arguments.get("max_preview_chars")},
        )
        max_preview_chars = constrained_args.get("max_preview_chars")
        if not isinstance(max_preview_chars, int) or max_preview_chars <= 0:
            max_preview_chars = 12000
        source_path = resolve_source_path(repo_root=repo_root, raw_path=target.strip())
        if not source_path.exists():
            return pack1_error(ErrorResponseDTO(code="ERR_FILE_NOT_FOUND", message="target file not found"))
        try:
            before_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return pack1_error(ErrorResponseDTO(code="ERR_TEXT_DECODE_FAILED", message="failed to read target file as utf-8"))
        preview_before = before_text[:max_preview_chars]
        preview_after = content[:max_preview_chars]
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode="diff_preview",
            target=normalize_source_path(repo_root=repo_root, source_path=source_path),
            content_text=f"{preview_before}\n{preview_after}",
            read_lines=max(1, preview_before.count("\n") + preview_after.count("\n")),
            read_span=max(1, preview_before.count("\n") + 1),
            warnings=soft_warnings,
            degraded=degraded,
        )
        return pack1_items_success(
            [
                {
                    "path": normalize_source_path(repo_root=repo_root, source_path=source_path),
                    "against": against,
                    "before_chars": len(before_text),
                    "after_chars": len(content),
                    "preview_before": preview_before[:400],
                    "preview_after": preview_after[:400],
                }
            ],
            stabilization=stabilization,
        )


class DryRunDiffTool:
    """dry_run_diff MCP 도구를 처리한다."""

    def __init__(self, read_tool: ReadTool) -> None:
        """read 도구 의존성을 주입한다."""
        self._read_tool = read_tool

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """legacy dry_run_diff 입력을 read(diff_preview)로 위임한다."""
        target = arguments.get("path")
        transformed = dict(arguments)
        transformed["mode"] = "diff_preview"
        transformed["target"] = target
        return self._read_tool.call(transformed)

