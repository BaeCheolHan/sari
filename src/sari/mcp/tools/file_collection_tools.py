"""MCP 파일 수집 도구(scan_once/list_files/read_file/index_file)를 제공한다."""

from __future__ import annotations

from sari.core.exceptions import CollectionError
from sari.core.models import ErrorResponseDTO
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.mcp.tools.arg_parser import parse_non_empty_string, parse_non_negative_int, parse_optional_int, parse_optional_string, parse_positive_int
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import Pack1MetaDTO, pack1_error, pack1_success
from sari.services.collection.ports import CollectionScanPort


def _collection_error_response(exc: CollectionError) -> dict[str, object]:
    """CollectionError를 pack1 명시적 오류 응답으로 변환한다."""
    return pack1_error(ErrorResponseDTO(code=exc.context.code, message=exc.context.message))


class ScanOnceTool:
    """scan_once MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, collection_service: CollectionScanPort) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._collection_service = collection_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """저장소 전체 1회 스캔을 실행한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])

        try:
            result = self._collection_service.scan_once(repo_root=repo)
        except CollectionError as exc:
            return _collection_error_response(exc)

        item = {
            "repo": repo,
            "scanned_count": result.scanned_count,
            "indexed_count": result.indexed_count,
            "deleted_count": result.deleted_count,
            "mode": result.mode,
            "target_repo_count": result.target_repo_count,
            "succeeded_repo_count": result.succeeded_repo_count,
            "failed_repo_count": result.failed_repo_count,
            "repo_results": [entry.to_dict() for entry in result.repo_results],
        }
        errors: list[dict[str, object]] = []
        for entry in result.repo_results:
            if entry.status != "error":
                continue
            errors.append(
                {
                    "code": entry.error_code or "ERR_SCAN_ONCE_FAILED",
                    "message": entry.error_message or "scan_once fan-out repository scan failed",
                    "repo_root": entry.repo_root,
                }
            )
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=result.scanned_count,
                    resolved_count=result.indexed_count,
                    cache_hit=None,
                    errors=errors,
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class ListFilesTool:
    """list_files MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, collection_service: CollectionScanPort) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._collection_service = collection_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파일 목록을 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])

        limit_raw, limit_error = parse_positive_int(arguments=arguments, key="limit", default=20)
        if limit_error is not None:
            return pack1_error(limit_error)
        prefix = parse_optional_string(arguments=arguments, key="prefix")

        try:
            items = self._collection_service.list_files(repo_root=repo, limit=limit_raw, prefix=prefix)
        except CollectionError as exc:
            return _collection_error_response(exc)

        return pack1_success(
            {
                "items": items,
                "meta": Pack1MetaDTO(
                    candidate_count=len(items),
                    resolved_count=len(items),
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class ReadFileTool:
    """read_file MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, collection_service: CollectionScanPort) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._collection_service = collection_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """파일 내용을 pack1 형식으로 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])

        relative_path_raw, path_error = parse_non_empty_string(arguments=arguments, key="relative_path")
        if path_error is not None:
            return pack1_error(path_error)
        offset_raw, offset_error = parse_non_negative_int(arguments=arguments, key="offset", default=0)
        if offset_error is not None:
            return pack1_error(offset_error)
        limit_raw, limit_error = parse_optional_int(arguments=arguments, key="limit", default=300)
        if limit_error is not None:
            return pack1_error(limit_error)

        try:
            result = self._collection_service.read_file(
                repo_root=repo,
                relative_path=relative_path_raw,
                offset=offset_raw,
                limit=limit_raw,
            )
        except CollectionError as exc:
            return _collection_error_response(exc)

        item = {
            "relative_path": result.relative_path,
            "content": result.content,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "source": result.source,
            "total_lines": result.total_lines,
            "is_truncated": result.is_truncated,
            "next_offset": result.next_offset,
        }
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=1,
                    cache_hit=result.source == "l2",
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )


class IndexFileTool:
    """index_file MCP 도구를 처리한다."""

    def __init__(self, workspace_repo: WorkspaceRepository, collection_service: CollectionScanPort) -> None:
        """필요 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._collection_service = collection_service

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """단일 파일 증분 인덱싱을 실행한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo = str(arguments["repo"])

        relative_path_raw, path_error = parse_non_empty_string(arguments=arguments, key="relative_path")
        if path_error is not None:
            return pack1_error(path_error)

        try:
            result = self._collection_service.index_file(repo_root=repo, relative_path=relative_path_raw)
        except CollectionError as exc:
            return _collection_error_response(exc)

        item = {
            "repo": repo,
            "relative_path": relative_path_raw,
            "scanned_count": result.scanned_count,
            "indexed_count": result.indexed_count,
            "deleted_count": result.deleted_count,
        }
        return pack1_success(
            {
                "items": [item],
                "meta": Pack1MetaDTO(
                    candidate_count=1,
                    resolved_count=result.indexed_count,
                    cache_hit=None,
                    errors=[],
                    warnings=warnings_payload,
                ).to_dict(),
            }
        )
