"""read 모드별 실행 로직을 담당한다."""

from __future__ import annotations

from dataclasses import dataclass

from sari.core.exceptions import CollectionError
from sari.core.models import ErrorResponseDTO
from sari.mcp.stabilization.ports import StabilizationPort
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.read_ports import ReadKnowledgePort, ReadLayerSymbolPort, ReadSymbolPort, ReadWorkspacePort
from sari.mcp.tools.row_mapper import rows_to_items
from sari.mcp.tools.tool_common import argument_error, normalize_source_path, resolve_source_path
from sari.services.collection.ports import CollectionScanPort


@dataclass(frozen=True)
class ReadExecutionResult:
    """read 성공 결과 DTO."""

    mode: str
    target: str
    content_text: str
    read_lines: int
    read_span: int
    warnings: list[str]
    degraded: bool
    cache_hit: bool
    items: list[dict[str, object]]


class ReadExecutor:
    """read 모드별 실행기."""

    def __init__(
        self,
        *,
        workspace_repo: ReadWorkspacePort,
        file_collection_service: CollectionScanPort,
        lsp_repo: ReadSymbolPort,
        knowledge_repo: ReadKnowledgePort,
        tool_layer_repo: ReadLayerSymbolPort | None,
        stabilization_service: StabilizationPort,
    ) -> None:
        self._workspace_repo = workspace_repo
        self._file_collection_service = file_collection_service
        self._lsp_repo = lsp_repo
        self._knowledge_repo = knowledge_repo
        self._tool_layer_repo = tool_layer_repo
        self._stabilization_service = stabilization_service

    def execute(
        self,
        *,
        repo_root: str,
        mode: str,
        arguments: dict[str, object],
    ) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        """mode에 맞는 실행 결과 또는 오류 payload를 반환한다."""
        if mode == "file":
            return self._read_file_mode(repo_root=repo_root, arguments=arguments)
        if mode == "symbol":
            return self._read_symbol_mode(repo_root=repo_root, arguments=arguments)
        if mode == "snippet":
            return self._read_snippet_mode(repo_root=repo_root, arguments=arguments)
        if mode == "diff_preview":
            return self._read_diff_preview_mode(repo_root=repo_root, arguments=arguments)
        return None, pack1_error(ErrorResponseDTO(code="ERR_UNSUPPORTED_MODE", message=f"unsupported mode: {mode}"))

    def _read_file_mode(self, *, repo_root: str, arguments: dict[str, object]) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return (
                None,
                argument_error(
                    code="ERR_TARGET_REQUIRED",
                    message="target is required",
                    arguments=arguments,
                    expected=["target"],
                    example={"repo": repo_root, "mode": "file", "target": "README.md"},
                ),
            )
        offset_raw = arguments.get("offset", 0)
        limit_raw = arguments.get("limit", 300)
        if not isinstance(offset_raw, int) or offset_raw < 0:
            return None, pack1_error(ErrorResponseDTO(code="ERR_INVALID_OFFSET", message="offset must be non-negative integer"))
        if limit_raw is not None and (not isinstance(limit_raw, int) or limit_raw <= 0):
            return None, pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer or null"))
        constrained_args, degraded, soft_warnings = self._stabilization_service.apply_soft_limits(
            mode="file",
            delegated_args={"offset": offset_raw, "limit": limit_raw},
        )
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
            return None, pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))
        return (
            ReadExecutionResult(
                mode="file",
                target=result.relative_path,
                content_text=result.content,
                read_lines=max(0, result.end_line - result.start_line + 1),
                read_span=max(0, result.end_line - result.start_line + 1),
                warnings=soft_warnings,
                degraded=degraded,
                cache_hit=result.source == "l2",
                items=[
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
            ),
            None,
        )

    def _read_symbol_mode(self, *, repo_root: str, arguments: dict[str, object]) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        target = arguments.get("target")
        if not isinstance(target, str) or target.strip() == "":
            return None, pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return None, pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        path_raw = arguments.get("path")
        path_prefix = path_raw if isinstance(path_raw, str) and path_raw.strip() != "" else None
        row_items: list[dict[str, object]] = []
        if self._tool_layer_repo is not None:
            workspace = self._workspace_repo.get_by_path(repo_root)
            if workspace is not None:
                row_items = self._tool_layer_repo.search_l3_symbols(
                    workspace_id=workspace.path,
                    repo_root=repo_root,
                    query=target.strip(),
                    limit=limit_raw,
                    path_prefix=path_prefix,
                )
        if len(row_items) == 0:
            rows = self._lsp_repo.search_symbols(repo_root=repo_root, query=target.strip(), limit=limit_raw, path_prefix=path_prefix)
            row_items = rows_to_items(rows)
        return (
            ReadExecutionResult(
                mode="symbol",
                target=target.strip(),
                content_text="\n".join(str(item.get("name", "")) for item in row_items),
                read_lines=len(row_items),
                read_span=len(row_items),
                warnings=[],
                degraded=False,
                cache_hit=True,
                items=row_items,
            ),
            None,
        )

    def _read_snippet_mode(self, *, repo_root: str, arguments: dict[str, object]) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        target = arguments.get("target")
        query = None if not isinstance(target, str) else target.strip()
        if query == "":
            query = None
        tag_raw = arguments.get("tag")
        tag = None if not isinstance(tag_raw, str) else tag_raw.strip()
        if tag == "":
            tag = None
        if tag is None and query is None:
            return (
                None,
                argument_error(
                    code="ERR_TARGET_REQUIRED",
                    message="target or tag is required for snippet mode",
                    arguments=arguments,
                    expected=["target", "tag"],
                    example={"repo": repo_root, "mode": "snippet", "target": "status_endpoint"},
                ),
            )
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return None, pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        constrained_args, degraded, soft_warnings = self._stabilization_service.apply_soft_limits(
            mode="snippet",
            delegated_args={"limit": limit_raw, "context_lines": arguments.get("context_lines")},
        )
        constrained_limit = constrained_args.get("limit", limit_raw)
        if not isinstance(constrained_limit, int) or constrained_limit <= 0:
            constrained_limit = limit_raw
        rows = self._knowledge_repo.query_snippets(repo_root=repo_root, tag=tag, query=query, limit=constrained_limit)
        row_items = rows_to_items(rows)
        target_value = query if query is not None else (tag if tag is not None else "")
        return (
            ReadExecutionResult(
                mode="snippet",
                target=target_value,
                content_text="\n".join(str(item.get("content", "")) for item in row_items),
                read_lines=len(row_items),
                read_span=len(row_items),
                warnings=soft_warnings,
                degraded=degraded,
                cache_hit=True,
                items=row_items,
            ),
            None,
        )

    def _read_diff_preview_mode(self, *, repo_root: str, arguments: dict[str, object]) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        target = arguments.get("target")
        content = arguments.get("content")
        if not isinstance(target, str) or target.strip() == "":
            return None, pack1_error(ErrorResponseDTO(code="ERR_TARGET_REQUIRED", message="target is required"))
        if not isinstance(content, str):
            return None, pack1_error(ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required"))
        against_raw = arguments.get("against", "WORKTREE")
        against = against_raw if isinstance(against_raw, str) and against_raw.strip() != "" else "WORKTREE"
        constrained_args, degraded, soft_warnings = self._stabilization_service.apply_soft_limits(
            mode="diff_preview",
            delegated_args={"max_preview_chars": arguments.get("max_preview_chars")},
        )
        max_preview_chars = constrained_args.get("max_preview_chars")
        if not isinstance(max_preview_chars, int) or max_preview_chars <= 0:
            max_preview_chars = 12000
        source_path = resolve_source_path(repo_root=repo_root, raw_path=target.strip())
        if not source_path.exists():
            return None, pack1_error(ErrorResponseDTO(code="ERR_FILE_NOT_FOUND", message="target file not found"))
        try:
            before_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None, pack1_error(ErrorResponseDTO(code="ERR_TEXT_DECODE_FAILED", message="failed to read target file as utf-8"))
        preview_before = before_text[:max_preview_chars]
        preview_after = content[:max_preview_chars]
        normalized_path = normalize_source_path(repo_root=repo_root, source_path=source_path)
        return (
            ReadExecutionResult(
                mode="diff_preview",
                target=normalized_path,
                content_text=f"{preview_before}\n{preview_after}",
                read_lines=max(1, preview_before.count("\n") + preview_after.count("\n")),
                read_span=max(1, preview_before.count("\n") + 1),
                warnings=soft_warnings,
                degraded=degraded,
                cache_hit=False,
                items=[
                    {
                        "path": normalized_path,
                        "against": against,
                        "before_chars": len(before_text),
                        "after_chars": len(content),
                        "preview_before": preview_before[:400],
                        "preview_after": preview_after[:400],
                    }
                ],
            ),
            None,
        )
