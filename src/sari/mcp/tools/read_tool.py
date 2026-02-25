"""read/dry_run_diff MCP 도구 구현."""

from __future__ import annotations

import concurrent.futures
import threading

from sari.core.models import ErrorResponseDTO
from sari.mcp.stabilization.ports import StabilizationPort
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.tools.admin_tools import validate_repo_argument
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.read_executor import ReadExecutionResult, ReadExecutor
from sari.mcp.tools.read_ports import ReadKnowledgePort, ReadLayerSymbolPort, ReadSymbolPort, ReadWorkspacePort
from sari.mcp.tools.read_request_parser import ReadRequestParser
from sari.mcp.tools.read_response_builder import ReadResponseBuilder
from sari.services.collection.ports import CollectionScanPort


class _ReadToolBusyError(RuntimeError):
    """read timeout gate가 이미 점유된 경우를 나타내는 내부 예외."""


class ReadTool:
    """read MCP unified 도구를 처리한다."""

    def __init__(
        self,
        workspace_repo: ReadWorkspacePort,
        file_collection_service: CollectionScanPort,
        lsp_repo: ReadSymbolPort,
        knowledge_repo: ReadKnowledgePort,
        tool_layer_repo: ReadLayerSymbolPort | None = None,
        stabilization_enabled: bool = True,
        call_timeout_sec: float = 0.0,
        stabilization_service: StabilizationPort | None = None,
    ) -> None:
        """필요 저장소/서비스 의존성을 주입한다."""
        self._workspace_repo = workspace_repo
        self._stabilization_service = (
            stabilization_service if stabilization_service is not None else StabilizationService(enabled=stabilization_enabled)
        )
        self._call_timeout_sec = max(0.0, float(call_timeout_sec))
        self._timeout_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._timeout_semaphore: threading.BoundedSemaphore | None = None
        if self._call_timeout_sec > 0:
            # NOTE: timeout enforcement uses a single persistent worker to avoid per-call thread leaks.
            self._timeout_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="read-tool")
            self._timeout_semaphore = threading.BoundedSemaphore(value=1)
        self._request_parser = ReadRequestParser()
        self._executor = ReadExecutor(
            workspace_repo=workspace_repo,
            file_collection_service=file_collection_service,
            lsp_repo=lsp_repo,
            knowledge_repo=knowledge_repo,
            tool_layer_repo=tool_layer_repo,
            stabilization_service=self._stabilization_service,
        )
        self._response_builder = ReadResponseBuilder()

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """모드별 read 응답을 반환한다."""
        validation = validate_repo_argument(arguments=arguments, workspace_repo=self._workspace_repo)
        if validation.error is not None:
            return pack1_error(validation.error)
        warnings_payload = [warning.to_dict() for warning in validation.warnings]
        repo_root = str(arguments["repo"])
        precheck = self._stabilization_service.precheck_read_call(arguments=arguments, repo_root=repo_root)
        if precheck.blocked:
            return pack1_error(
                ErrorResponseDTO(code=str(precheck.error_code), message=str(precheck.error_message)),
                stabilization=precheck.meta,
            )
        parsed, parse_error = self._request_parser.parse(arguments=arguments, repo_root=repo_root)
        if parse_error is not None:
            return parse_error
        assert parsed is not None
        try:
            execution, execution_error = self._run_with_timeout(
                repo_root=parsed.repo_root,
                mode=parsed.mode,
                arguments=arguments,
            )
        except TimeoutError:
            return pack1_error(
                ErrorResponseDTO(code="ERR_TOOL_TIMEOUT", message="read timed out"),
                recovery_hint="요청 범위를 줄이거나 limit를 낮춘 뒤 재시도하세요.",
            )
        except _ReadToolBusyError:
            return pack1_error(
                ErrorResponseDTO(code="ERR_TOOL_BUSY", message="read worker busy"),
                recovery_hint="직전 요청이 아직 처리 중입니다. 잠시 후 재시도하세요.",
            )
        if execution_error is not None:
            return execution_error
        assert execution is not None
        stabilization = self._build_stabilization_meta(
            arguments=arguments,
            repo_root=parsed.repo_root,
            execution=execution,
        )
        return self._response_builder.build_success(
            execution=execution,
            warnings_payload=warnings_payload,
            stabilization=stabilization,
        )

    def _run_with_timeout(
        self,
        *,
        repo_root: str,
        mode: str,
        arguments: dict[str, object],
    ) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        if self._call_timeout_sec <= 0:
            return self._executor.execute(repo_root=repo_root, mode=mode, arguments=arguments)
        assert self._timeout_executor is not None
        assert self._timeout_semaphore is not None
        if not self._timeout_semaphore.acquire(blocking=False):
            raise _ReadToolBusyError("read tool worker busy")
        try:
            future = self._timeout_executor.submit(self._run_read_task, repo_root=repo_root, mode=mode, arguments=arguments)
        except Exception:
            self._timeout_semaphore.release()
            raise
        try:
            return future.result(timeout=self._call_timeout_sec)
        except concurrent.futures.TimeoutError as exc:
            canceled = future.cancel()
            if canceled:
                # Task never started; release gate here to avoid permanent busy state.
                self._timeout_semaphore.release()
            raise TimeoutError("read tool timed out") from exc

    def _run_read_task(
        self,
        *,
        repo_root: str,
        mode: str,
        arguments: dict[str, object],
    ) -> tuple[ReadExecutionResult | None, dict[str, object] | None]:
        """단일 worker 내 실제 read 호출을 수행한다."""
        try:
            return self._executor.execute(repo_root=repo_root, mode=mode, arguments=arguments)
        finally:
            assert self._timeout_semaphore is not None
            self._timeout_semaphore.release()

    def _build_stabilization_meta(
        self,
        *,
        arguments: dict[str, object],
        repo_root: str,
        execution: ReadExecutionResult,
    ) -> dict[str, object] | None:
        """read 성공 응답용 stabilization 메타를 생성한다."""
        return self._stabilization_service.build_read_success_meta(
            arguments=arguments,
            repo_root=repo_root,
            mode=execution.mode,
            target=execution.target,
            content_text=execution.content_text,
            read_lines=execution.read_lines,
            read_span=execution.read_span,
            warnings=execution.warnings,
            degraded=execution.degraded,
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
