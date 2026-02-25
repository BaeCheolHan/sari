"""knowledge/snippet/context MCP 도구 구현."""

from __future__ import annotations

from sari.core.models import ErrorResponseDTO, KnowledgeEntryDTO, SnippetSaveDTO, now_iso8601_utc
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.mcp.tools.admin_tools import RepoValidationPort, validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.row_mapper import rows_to_items
from sari.mcp.tools.tool_common import normalize_source_path, pack1_items_success, resolve_source_path


class KnowledgeTool:
    """knowledge MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """지식 엔트리 조회 결과를 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        query_raw = arguments.get("query")
        query = query_raw if isinstance(query_raw, str) else None
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_knowledge(repo_root=str(arguments["repo"]), kind="knowledge", query=query, limit=limit_raw)
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)


class SaveSnippetTool:
    """save_snippet MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파일 구간 스니펫을 저장한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo_root = str(arguments["repo"])
        path_raw = arguments.get("path")
        if not isinstance(path_raw, str) or path_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_PATH_REQUIRED", message="path is required"))
        start_line_raw = arguments.get("start_line")
        end_line_raw = arguments.get("end_line")
        if not isinstance(start_line_raw, int) or not isinstance(end_line_raw, int):
            return pack1_error(ErrorResponseDTO(code="ERR_LINE_RANGE_REQUIRED", message="start_line/end_line are required"))
        if start_line_raw <= 0 or end_line_raw < start_line_raw:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LINE_RANGE", message="invalid line range"))
        tag_raw = arguments.get("tag")
        if not isinstance(tag_raw, str) or tag_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TAG_REQUIRED", message="tag is required"))
        source_path = resolve_source_path(repo_root=repo_root, raw_path=path_raw.strip())
        if not source_path.exists() or not source_path.is_file():
            return pack1_error(ErrorResponseDTO(code="ERR_FILE_NOT_FOUND", message="source file not found"))
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return pack1_error(ErrorResponseDTO(code="ERR_TEXT_DECODE_FAILED", message="failed to read source file as utf-8"))
        if end_line_raw > len(lines):
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LINE_RANGE", message="line range out of bound"))
        content = "\n".join(lines[start_line_raw - 1 : end_line_raw])
        snippet_id = self._knowledge_repo.save_snippet(
            SnippetSaveDTO(
                repo_root=repo_root,
                source_path=normalize_source_path(repo_root=repo_root, source_path=source_path),
                start_line=start_line_raw,
                end_line=end_line_raw,
                tag=tag_raw.strip(),
                note=arguments.get("note") if isinstance(arguments.get("note"), str) else None,
                commit_hash=arguments.get("commit") if isinstance(arguments.get("commit"), str) else None,
                content_text=content,
                created_at=now_iso8601_utc(),
            )
        )
        return pack1_items_success([{"snippet_id": snippet_id, "tag": tag_raw.strip()}], warnings=warnings_payload)


class GetSnippetTool:
    """get_snippet MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장된 스니펫을 조회한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        tag = arguments.get("tag") if isinstance(arguments.get("tag"), str) else None
        query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
        if (tag is None or tag.strip() == "") and (query is None or query.strip() == ""):
            return pack1_error(ErrorResponseDTO(code="ERR_QUERY_REQUIRED", message="tag or query is required"))
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_snippets(
            repo_root=str(arguments["repo"]),
            tag=None if tag is None else tag.strip(),
            query=None if query is None else query.strip(),
            limit=limit_raw,
        )
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)


class ArchiveContextTool:
    """archive_context MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """문맥 정보를 보존 저장한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        topic_raw = arguments.get("topic")
        content_raw = arguments.get("content")
        if not isinstance(topic_raw, str) or topic_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_TOPIC_REQUIRED", message="topic is required"))
        if not isinstance(content_raw, str) or content_raw.strip() == "":
            return pack1_error(ErrorResponseDTO(code="ERR_CONTENT_REQUIRED", message="content is required"))
        tags_raw = arguments.get("tags")
        tags: list[str] = []
        if isinstance(tags_raw, list):
            for item in tags_raw:
                if isinstance(item, str) and item.strip() != "":
                    tags.append(item.strip())
        files_raw = arguments.get("related_files")
        related_files: list[str] = []
        if isinstance(files_raw, list):
            for item in files_raw:
                if isinstance(item, str) and item.strip() != "":
                    related_files.append(item.strip())
        entry_id = self._knowledge_repo.archive_knowledge(
            KnowledgeEntryDTO(
                kind="context",
                repo_root=str(arguments["repo"]),
                topic=topic_raw.strip(),
                content_text=content_raw,
                tags=tuple(tags),
                related_files=tuple(related_files),
                created_at=now_iso8601_utc(),
            )
        )
        return pack1_items_success([{"entry_id": entry_id, "topic": topic_raw.strip()}], warnings=warnings_payload)


class GetContextTool:
    """get_context MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: RepoValidationPort, knowledge_repo: KnowledgeRepository) -> None:
        """필요 저장소 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._knowledge_repo = knowledge_repo

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장된 문맥 엔트리를 조회한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        query = arguments.get("query") if isinstance(arguments.get("query"), str) else None
        limit_raw = arguments.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            return pack1_error(ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"))
        rows = self._knowledge_repo.query_knowledge(repo_root=str(arguments["repo"]), kind="context", query=query, limit=limit_raw)
        return pack1_items_success(rows_to_items(rows), cache_hit=True, warnings=warnings_payload)
