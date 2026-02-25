"""MCP search 도구를 구현한다."""

from __future__ import annotations

import concurrent.futures
import inspect
import logging
import threading
from collections.abc import Callable

from sari.core.models import ErrorResponseDTO
from sari.core.repo_context_resolver import resolve_repo_context
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.mcp.tools.admin_tools import RepoValidationPort
from sari.mcp.tools.pack1_builder import Pack1EnvelopeBuilder
from sari.mcp.stabilization.ports import StabilizationPort
from sari.mcp.stabilization.stabilization_service import StabilizationService
from sari.mcp.tools.search_item_serializer import SearchItemSerializer
from sari.mcp.tools.search_response_builder import SearchResponseBuilder
from sari.search.orchestrator import SearchOrchestrator

log = logging.getLogger(__name__)


class _SearchToolBusyError(RuntimeError):
    """search timeout gate가 이미 점유된 경우를 나타내는 내부 예외."""


class SearchTool:
    """pack1 계약 기반 search 도구를 제공한다."""

    def __init__(
        self,
        orchestrator: SearchOrchestrator,
        workspace_repo: RepoValidationPort | None = None,
        tool_layer_repo: ToolDataLayerRepository | None = None,
        metrics_provider: Callable[[], object] | None = None,
        repo_registry_repo: RepoRegistryRepository | None = None,
        stabilization_enabled: bool = True,
        include_info_default: bool = False,
        symbol_info_budget_sec_default: float = 10.0,
        call_timeout_sec: float = 0.0,
        resolve_symbols_default_provider: Callable[[], bool] | None = None,
        stabilization_service: StabilizationPort | None = None,
    ) -> None:
        """검색 오케스트레이터를 주입한다."""
        self._orchestrator = orchestrator
        self._workspace_repo = workspace_repo
        self._tool_layer_repo = tool_layer_repo
        self._metrics_provider = metrics_provider
        self._repo_registry_repo = repo_registry_repo
        self._include_info_default = bool(include_info_default)
        self._symbol_info_budget_sec_default = max(0.0, float(symbol_info_budget_sec_default))
        self._call_timeout_sec = max(0.0, float(call_timeout_sec))
        self._resolve_symbols_default_provider = resolve_symbols_default_provider
        self._stabilization_service = (
            stabilization_service if stabilization_service is not None else StabilizationService(enabled=stabilization_enabled)
        )
        signature = inspect.signature(self._orchestrator.search)
        self._search_params = set(signature.parameters.keys())
        self._timeout_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._timeout_semaphore: threading.BoundedSemaphore | None = None
        if self._call_timeout_sec > 0:
            # NOTE: timeout enforcement uses a single persistent worker to avoid per-call thread leaks.
            self._timeout_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="search-tool")
            self._timeout_semaphore = threading.BoundedSemaphore(value=1)
        self._envelope_builder = Pack1EnvelopeBuilder()
        self._response_builder = SearchResponseBuilder(
            envelope_builder=self._envelope_builder,
            item_serializer=SearchItemSerializer(workspace_repo=self._workspace_repo, tool_layer_repo=self._tool_layer_repo),
        )

    def call(self, arguments: dict[str, object]) -> dict[str, object]:
        """도구 입력을 검증하고 pack1 결과를 반환한다."""
        repo = arguments.get("repo")
        query = arguments.get("query")
        limit = arguments.get("limit", 20)
        include_info_raw = arguments.get("include_info", None)
        symbol_info_budget_raw = arguments.get("symbol_info_budget_sec", None)
        resolve_symbols_raw = arguments.get("resolve_symbols", None)

        if not isinstance(repo, str) or repo.strip() == "":
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_REPO_REQUIRED", message="repo is required"),
                recovery_hint="search 호출 시 repo 파라미터를 반드시 제공해야 합니다.",
            )
        repo_id: str | None = None
        if self._workspace_repo is not None:
            context, context_error = resolve_repo_context(
                raw_repo=repo.strip(),
                workspace_repo=self._workspace_repo,
                repo_registry_repo=self._repo_registry_repo,
                allow_absolute_input=True,
            )
            if context_error is not None:
                return self._envelope_builder.build_error(error=context_error)
            assert context is not None
            repo = context.repo_root
            repo_id = context.repo_id
        if not isinstance(query, str) or query.strip() == "":
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_QUERY_REQUIRED", message="query is required"),
                recovery_hint="search 호출 시 query 파라미터를 반드시 제공해야 합니다.",
            )
        if not isinstance(limit, int) or limit <= 0:
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_INVALID_LIMIT", message="limit must be positive integer"),
                recovery_hint="limit은 1 이상의 정수여야 합니다.",
            )
        if include_info_raw is not None and not isinstance(include_info_raw, bool):
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_INVALID_INCLUDE_INFO", message="include_info must be boolean"),
                recovery_hint="include_info는 true/false 불리언이어야 합니다.",
            )
        if symbol_info_budget_raw is not None and not isinstance(symbol_info_budget_raw, (int, float)):
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_INVALID_SYMBOL_INFO_BUDGET", message="symbol_info_budget_sec must be number"),
                recovery_hint="symbol_info_budget_sec는 0 이상의 숫자여야 합니다.",
            )
        if resolve_symbols_raw is not None and not isinstance(resolve_symbols_raw, bool):
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_INVALID_RESOLVE_SYMBOLS", message="resolve_symbols must be boolean"),
                recovery_hint="resolve_symbols는 true/false 불리언이어야 합니다.",
            )
        include_info = self._include_info_default if include_info_raw is None else bool(include_info_raw)
        default_resolve_symbols = False
        if self._resolve_symbols_default_provider is not None:
            try:
                default_resolve_symbols = bool(self._resolve_symbols_default_provider())
            except (RuntimeError, OSError, ValueError, TypeError):
                default_resolve_symbols = False
        resolve_symbols = default_resolve_symbols if resolve_symbols_raw is None else bool(resolve_symbols_raw)
        symbol_info_budget_sec = (
            self._symbol_info_budget_sec_default
            if symbol_info_budget_raw is None
            else max(0.0, float(symbol_info_budget_raw))
        )

        try:
            result = self._run_search_with_timeout(
                query=query,
                limit=limit,
                repo=repo,
                repo_id=repo_id,
                resolve_symbols=resolve_symbols,
                include_info=include_info,
                symbol_info_budget_sec=symbol_info_budget_sec,
            )
        except TimeoutError:
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_TOOL_TIMEOUT", message="search timed out"),
                recovery_hint="query 범위를 줄이거나 limit를 낮춘 뒤 재시도하세요.",
            )
        except _SearchToolBusyError:
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code="ERR_TOOL_BUSY", message="search worker busy"),
                recovery_hint="직전 요청이 아직 처리 중입니다. 잠시 후 재시도하세요.",
            )
        stabilization_meta = self._stabilization_service.build_search_success_meta(
            arguments=arguments,
            repo=repo,
            query=query,
            items=result.items,
            degraded=result.meta.degraded,
            fatal_error=result.meta.fatal_error,
            errors=[error.to_dict() for error in result.meta.errors],
        )
        progress_meta = self._build_progress_meta()
        if result.meta.fatal_error:
            first_error = result.meta.errors[0]
            recovery_hint = _resolve_recovery_hint(first_error.code)
            return self._envelope_builder.build_error(
                error=ErrorResponseDTO(code=first_error.code, message=first_error.message),
                detailed_errors=[error.to_dict() for error in result.meta.errors],
                stabilization=stabilization_meta,
                recovery_hint=recovery_hint,
            )
        return self._response_builder.build_success(
            result=result,
            repo_root=repo,
            stabilization=stabilization_meta,
            progress_meta=progress_meta,
        )

    def _run_search_with_timeout(
        self,
        *,
        query: str,
        limit: int,
        repo: str,
        repo_id: str | None,
        resolve_symbols: bool,
        include_info: bool,
        symbol_info_budget_sec: float,
    ) -> object:
        kwargs: dict[str, object] = {"query": query, "limit": limit, "repo_root": repo}
        if "repo_id" in self._search_params:
            kwargs["repo_id"] = repo_id
        if "resolve_symbols" in self._search_params:
            kwargs["resolve_symbols"] = resolve_symbols
        if "include_info" in self._search_params:
            kwargs["include_info"] = include_info
        if "symbol_info_budget_sec" in self._search_params:
            kwargs["symbol_info_budget_sec"] = symbol_info_budget_sec
        if self._call_timeout_sec <= 0:
            return self._orchestrator.search(**kwargs)
        assert self._timeout_executor is not None
        assert self._timeout_semaphore is not None
        if not self._timeout_semaphore.acquire(blocking=False):
            raise _SearchToolBusyError("search tool worker busy")
        try:
            future = self._timeout_executor.submit(self._run_search_task, kwargs)
        except RuntimeError:
            self._timeout_semaphore.release()
            raise
        try:
            return future.result(timeout=self._call_timeout_sec)
        except concurrent.futures.TimeoutError as exc:
            canceled = future.cancel()
            if canceled:
                # Task never started; release gate here to avoid permanent busy state.
                self._timeout_semaphore.release()
            raise TimeoutError("search tool timed out") from exc

    def _run_search_task(self, kwargs: dict[str, object]) -> object:
        """단일 worker 내 실제 search 호출을 수행한다."""
        try:
            return self._orchestrator.search(**kwargs)
        finally:
            assert self._timeout_semaphore is not None
            self._timeout_semaphore.release()

    def _build_progress_meta(self) -> dict[str, object] | None:
        """파이프라인 진행률 메타를 반환한다."""
        if self._metrics_provider is None:
            return None
        try:
            metrics = self._metrics_provider()
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            reason = f"metrics_provider_error:{type(exc).__name__}"
            log.debug("failed to collect search progress metrics", exc_info=True)
            return {
                "progress_percent_l2": 0.0,
                "progress_percent_l3": 0.0,
                "eta_l2_sec": -1,
                "eta_l3_sec": -1,
                "remaining_jobs_l2": 0,
                "remaining_jobs_l3": 0,
                "worker_state": "unknown",
                "error_reason": reason,
            }
        if not hasattr(metrics, "to_dict"):
            return {
                "progress_percent_l2": 0.0,
                "progress_percent_l3": 0.0,
                "eta_l2_sec": -1,
                "eta_l3_sec": -1,
                "remaining_jobs_l2": 0,
                "remaining_jobs_l3": 0,
                "worker_state": "unknown",
                "error_reason": "metrics_provider_missing_to_dict",
            }
        payload = metrics.to_dict()
        return {
            "progress_percent_l2": float(payload.get("progress_percent_l2", 0.0)),
            "progress_percent_l3": float(payload.get("progress_percent_l3", 0.0)),
            "eta_l2_sec": int(payload.get("eta_l2_sec", -1)),
            "eta_l3_sec": int(payload.get("eta_l3_sec", -1)),
            "remaining_jobs_l2": int(payload.get("remaining_jobs_l2", 0)),
            "remaining_jobs_l3": int(payload.get("remaining_jobs_l3", 0)),
            "worker_state": str(payload.get("worker_state", "unknown")),
        }


def _resolve_recovery_hint(error_code: str) -> str | None:
    """치명 오류 코드별 운영 복구 힌트를 생성한다."""
    if error_code == "ERR_LSP_UNAVAILABLE":
        return (
            "LSP 서버가 기동되지 않았습니다. sari doctor 및 pipeline lsp-matrix diagnose로 "
            "언어별 상태를 확인하고 필요한 서버를 설치하세요."
        )
    if error_code == "ERR_REPO_NOT_REGISTERED":
        return "해당 경로를 roots add로 등록한 뒤 다시 검색하세요."
    if error_code == "ERR_TANTIVY_LOCK_BUSY":
        return "현재 DB/인덱스를 다른 프로세스가 사용 중입니다. `sari mcp stdio`(proxy 기본 경로)로 연결하거나, 데몬을 단일 경로로 사용하세요."
    return None
