"""status 응답의 language support 계약을 검증한다."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.core.models import WorkspaceDTO
from sari.db.schema import init_schema
from sari.http.app import HttpContext, status_endpoint
from sari.mcp.tools.status_tool import StatusTool
from sari.services.pipeline_control_service import PipelineControlService
from sari.db.repositories.pipeline_policy_repository import PipelinePolicyRepository
from sari.db.repositories.pipeline_job_event_repository import PipelineJobEventRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.pipeline_control_state_repository import PipelineControlStateRepository


class _AdminServiceStub:
    """status 엔드포인트 테스트용 admin service 스텁."""

    def run_mode(self) -> str:
        """현재 모드를 반환한다."""
        return "prod"

    def get_runtime_reconcile_state(self) -> dict[str, object]:
        """마지막 reconcile 상태를 반환한다."""
        return {"reconcile_last_run_ts": None, "reconcile_last_result": None}


def _build_pipeline_control_service(db_path: Path) -> PipelineControlService:
    return PipelineControlService(
        policy_repo=PipelinePolicyRepository(db_path),
        event_repo=PipelineJobEventRepository(db_path),
        queue_repo=FileEnrichQueueRepository(db_path),
        control_state_repo=PipelineControlStateRepository(db_path),
    )


def test_http_status_exposes_language_readiness_snapshot(tmp_path: Path) -> None:
    """HTTP status는 언어 readiness 스냅샷 목록을 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    probe_repo = LanguageProbeRepository(db_path)
    probe_repo.upsert_result(
        language="python",
        enabled=True,
        available=False,
        last_probe_at="2026-02-17T00:00:00+00:00",
        last_error_code="ERR_LSP_UNAVAILABLE",
        last_error_message="pyright not installed",
    )

    context = HttpContext(
        runtime_repo=RuntimeRepository(db_path),
        workspace_repo=WorkspaceRepository(db_path),
        search_orchestrator=SimpleNamespace(),
        admin_service=_AdminServiceStub(),
        pipeline_control_service=_build_pipeline_control_service(db_path),
        language_probe_repo=probe_repo,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(context=context)))
    response = asyncio.run(status_endpoint(request))

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["run_mode"] == "prod"
    language_support = payload["language_support"]
    assert "languages" in language_support
    assert isinstance(language_support["languages"], list)
    assert len(language_support["languages"]) >= 1
    python_rows = [item for item in language_support["languages"] if item["language"] == "python"]
    assert len(python_rows) == 1
    first = python_rows[0]
    assert first["enabled"] is True
    assert first["available"] is False
    assert first["last_error_code"] == "ERR_LSP_UNAVAILABLE"
    assert first["last_error_message"] == "pyright not installed"
    assert "stage_rollout" in payload
    assert isinstance(payload["stage_rollout"], dict)


def test_mcp_status_exposes_language_readiness_snapshot(tmp_path: Path) -> None:
    """MCP status 도구도 language readiness 목록을 포함해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir(parents=True, exist_ok=True)
    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(repo_root.resolve()),
            name=repo_root.name,
            indexed_at=None,
            is_active=True,
        )
    )
    probe_repo = LanguageProbeRepository(db_path)
    probe_repo.upsert_result(
        language="python",
        enabled=True,
        available=True,
        last_probe_at="2026-02-17T00:00:00+00:00",
        last_error_code=None,
        last_error_message=None,
    )
    pipeline_control_service = _build_pipeline_control_service(db_path)
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        language_probe_repo=probe_repo,
        pipeline_control_service=pipeline_control_service,
        lsp_metrics_provider=lambda: {
            "lsp_instance_count": 4,
            "lsp_forced_kill_count": 1,
            "lsp_stop_timeout_count": 0,
            "lsp_orphan_suspect_count": 2,
        },
        reconcile_state_provider=lambda: {
            "reconcile_last_run_ts": "2026-02-19T12:00:00+00:00",
            "reconcile_last_result": "ok",
        },
    )
    payload = tool.call({"repo": str(repo_root.resolve())})

    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    language_support = item["language_support"]
    assert len(language_support["languages"]) >= 1
    assert any(entry["language"] == "python" for entry in language_support["languages"])
    assert item["lsp_metrics"]["lsp_instance_count"] == 4
    assert item["lsp_metrics"]["lsp_orphan_suspect_count"] == 2
    assert item["reconcile_state"]["reconcile_last_run_ts"] == "2026-02-19T12:00:00+00:00"
    assert "stage_rollout" in item
    assert isinstance(item["stage_rollout"], dict)
