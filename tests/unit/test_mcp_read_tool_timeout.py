"""read 도구 실행 타임아웃 동작을 검증한다."""

from __future__ import annotations

import concurrent.futures
import time

from sari.core.models import FileReadResultDTO, WorkspaceDTO
from sari.mcp.tools.read_tool import ReadTool


class _WorkspaceRepoStub:
    def __init__(self, repo_root: str) -> None:
        self._workspace = WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True)

    def get_by_path(self, path: str) -> WorkspaceDTO | None:
        return self._workspace if path == self._workspace.path else None

    def list_all(self) -> list[WorkspaceDTO]:
        return [self._workspace]


class _SlowCollectionServiceStub:
    def read_file(self, repo_root: str, relative_path: str, offset: int, limit: int | None) -> FileReadResultDTO:
        del repo_root, relative_path, offset, limit
        time.sleep(0.2)
        return FileReadResultDTO(
            relative_path="a.py",
            content="",
            start_line=1,
            end_line=1,
            source="l2",
            total_lines=1,
            is_truncated=False,
            next_offset=None,
        )


class _SymbolRepoStub:
    def search_symbols(
        self,
        repo_root: str,
        query: str,
        limit: int,
        path_prefix: str | None = None,
    ) -> list[object]:
        del repo_root, query, limit, path_prefix
        return []


class _KnowledgeRepoStub:
    def query_snippets(self, repo_root: str, tag: str | None, query: str | None, limit: int) -> list[object]:
        del repo_root, tag, query, limit
        return []


def test_read_tool_returns_timeout_error_when_execution_exceeds_budget() -> None:
    repo_root = "/repo"
    tool = ReadTool(
        workspace_repo=_WorkspaceRepoStub(repo_root),
        file_collection_service=_SlowCollectionServiceStub(),
        lsp_repo=_SymbolRepoStub(),
        knowledge_repo=_KnowledgeRepoStub(),
        call_timeout_sec=0.05,
    )

    payload = tool.call(
        {
            "repo": repo_root,
            "mode": "file",
            "target": "a.py",
        }
    )
    assert payload["isError"] is True
    assert payload["structuredContent"]["error"]["code"] == "ERR_TOOL_TIMEOUT"


def test_read_tool_returns_busy_when_previous_timed_out_task_still_running() -> None:
    repo_root = "/repo"
    tool = ReadTool(
        workspace_repo=_WorkspaceRepoStub(repo_root),
        file_collection_service=_SlowCollectionServiceStub(),
        lsp_repo=_SymbolRepoStub(),
        knowledge_repo=_KnowledgeRepoStub(),
        call_timeout_sec=0.05,
    )
    first = tool.call({"repo": repo_root, "mode": "file", "target": "a.py"})
    second = tool.call({"repo": repo_root, "mode": "file", "target": "a.py"})

    assert first["isError"] is True
    assert first["structuredContent"]["error"]["code"] == "ERR_TOOL_TIMEOUT"
    assert second["isError"] is True
    assert second["structuredContent"]["error"]["code"] == "ERR_TOOL_BUSY"


def test_read_tool_releases_gate_when_timeout_cancels_before_start() -> None:
    """timeout에서 cancel=True(미시작 취소)면 다음 호출이 busy에 빠지지 않아야 한다."""

    class _CancelBeforeStartFuture:
        def result(self, timeout: float | None = None) -> object:
            del timeout
            raise concurrent.futures.TimeoutError()

        def cancel(self) -> bool:
            return True

    class _FakeExecutor:
        def submit(self, fn, **kwargs):  # type: ignore[no-untyped-def]
            del fn, kwargs
            return _CancelBeforeStartFuture()

    repo_root = "/repo"
    tool = ReadTool(
        workspace_repo=_WorkspaceRepoStub(repo_root),
        file_collection_service=_SlowCollectionServiceStub(),
        lsp_repo=_SymbolRepoStub(),
        knowledge_repo=_KnowledgeRepoStub(),
        call_timeout_sec=0.01,
    )
    tool._timeout_executor = _FakeExecutor()  # type: ignore[assignment]

    first = tool.call({"repo": repo_root, "mode": "file", "target": "a.py"})
    second = tool.call({"repo": repo_root, "mode": "file", "target": "a.py"})
    assert first["isError"] is True
    assert first["structuredContent"]["error"]["code"] == "ERR_TOOL_TIMEOUT"
    assert second["isError"] is True
    assert second["structuredContent"]["error"]["code"] == "ERR_TOOL_TIMEOUT"


def test_read_tool_does_not_map_general_runtime_error_to_busy() -> None:
    """일반 RuntimeError는 ERR_TOOL_BUSY로 오분류되면 안 된다."""
    repo_root = "/repo"
    tool = ReadTool(
        workspace_repo=_WorkspaceRepoStub(repo_root),
        file_collection_service=_SlowCollectionServiceStub(),
        lsp_repo=_SymbolRepoStub(),
        knowledge_repo=_KnowledgeRepoStub(),
    )

    def _raise_runtime_error(*, repo_root: str, mode: str, arguments: dict[str, object]):  # type: ignore[no-untyped-def]
        del repo_root, mode, arguments
        raise RuntimeError("unexpected read runtime error")

    tool._executor.execute = _raise_runtime_error  # type: ignore[method-assign]
    try:
        tool.call({"repo": repo_root, "mode": "file", "target": "a.py"})
    except RuntimeError as exc:
        assert "read runtime error" in str(exc)
    else:
        raise AssertionError("RuntimeError should propagate and must not be mapped to ERR_TOOL_BUSY")
