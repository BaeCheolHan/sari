"""MCP 도구별 repo validation warning 전파를 검증한다."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sari.core.models import WorkspaceDTO
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.mcp.tools.pipeline_admin_tools import PipelinePolicyGetTool
from sari.mcp.tools.pipeline_lsp_matrix_tools import PipelineLspMatrixReportTool
from sari.mcp.tools.pipeline_perf_tools import PipelinePerfReportTool
from sari.mcp.tools.pipeline_quality_tools import PipelineQualityReportTool
from sari.mcp.tools.status_tool import StatusTool
from sari.mcp.tools.symbol_graph_tools import CallGraphTool


def _register_workspace(db_path: Path, repo_path: Path) -> None:
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(repo_path.resolve()),
            name=repo_path.name,
            indexed_at=None,
            is_active=True,
        )
    )


def _assert_partial_fallback_warning(response: dict[str, object]) -> None:
    assert response["isError"] is False
    structured = response["structuredContent"]
    meta = structured["meta"]
    warnings = meta.get("warnings")
    assert isinstance(warnings, list)
    assert warnings[0]["code"] == "WARN_REPO_ARG_PARTIAL_FALLBACK"


def test_status_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    tool = StatusTool(
        workspace_repo=WorkspaceRepository(db_path),
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
    )
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo"})
    _assert_partial_fallback_warning(response)


def test_pipeline_policy_get_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    service = SimpleNamespace(get_policy=lambda: SimpleNamespace(to_dict=lambda: {"deletion_hold": False}))
    tool = PipelinePolicyGetTool(workspace_repo=WorkspaceRepository(db_path), service=service)  # type: ignore[arg-type]
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo"})
    _assert_partial_fallback_warning(response)


def test_pipeline_perf_report_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    perf_service = SimpleNamespace(get_latest_report=lambda repo_root: {"report_id": "latest", "repo_root": repo_root})
    tool = PipelinePerfReportTool(workspace_repo=WorkspaceRepository(db_path), perf_service=perf_service)  # type: ignore[arg-type]
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo"})
    _assert_partial_fallback_warning(response)


def test_pipeline_quality_report_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    quality_service = SimpleNamespace(get_latest_report=lambda repo_root: {"repo": repo_root, "quality": "ok"})
    tool = PipelineQualityReportTool(workspace_repo=WorkspaceRepository(db_path), quality_service=quality_service)  # type: ignore[arg-type]
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo"})
    _assert_partial_fallback_warning(response)


def test_pipeline_lsp_matrix_report_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    matrix_service = SimpleNamespace(get_latest_report=lambda repo_root: {"repo": repo_root, "ready": True})
    tool = PipelineLspMatrixReportTool(workspace_repo=WorkspaceRepository(db_path), matrix_service=matrix_service)  # type: ignore[arg-type]
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo"})
    _assert_partial_fallback_warning(response)


def test_call_graph_tool_includes_validation_warnings_in_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir()
    _register_workspace(db_path=db_path, repo_path=repo_dir)

    tool = CallGraphTool(workspace_repo=WorkspaceRepository(db_path), lsp_repo=LspToolDataRepository(db_path))
    response = tool.call({"repo": "repo-a", "repo_key": "missing-repo", "symbol": "AuthService.login"})
    _assert_partial_fallback_warning(response)
