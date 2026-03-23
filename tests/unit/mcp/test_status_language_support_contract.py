"""status 응답의 language support 계약을 검증한다."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sari.db.repositories.language_probe_repository import LanguageProbeRepository
from sari.db.repositories.repo_language_probe_repository import RepoLanguageProbeRepository
from sari.db.repositories.repo_registry_repository import RepoRegistryRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.core.models import WorkspaceDTO
from sari.core.models import PipelineMetricsDTO
from sari.core.models import RepoIdentityDTO
from sari.core.models import now_iso8601_utc
from sari.db.schema import init_schema
from sari.db.schema import connect
from sari.http.app import HttpContext, status_endpoint
from sari.mcp.tools.status_tool import StatusTool
from sari.services.pipeline.control_service import PipelineControlService
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


class _FileCollectionServiceStub:
    def get_pipeline_metrics(self) -> PipelineMetricsDTO:
        return PipelineMetricsDTO(queue_depth=0, running_jobs=0, failed_jobs=0, dead_jobs=0, done_jobs=0, avg_enrich_latency_ms=0.0)

    def get_l5_admission_status(self) -> dict[str, object]:
        return {
            "shadow_enabled": True,
            "enforced": False,
            "limits": {"call_rate_total_max": 0.05, "call_rate_batch_max": 0.01},
            "metrics": {
                "l5_total_decisions": 100.0,
                "l5_total_admitted": 4.0,
                "l5_batch_decisions": 80.0,
                "l5_batch_admitted": 1.0,
                "l5_reject_count_by_reject_reason_mode_not_allowed": 70.0,
            },
        }


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
        last_error_message="pyrefly not installed",
    )

    context = HttpContext(
        runtime_repo=RuntimeRepository(db_path),
        workspace_repo=WorkspaceRepository(db_path),
        search_orchestrator=SimpleNamespace(),
        admin_service=_AdminServiceStub(),
        file_collection_service=_FileCollectionServiceStub(),
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
    assert first["last_error_message"] == "pyrefly not installed"
    assert "stage_rollout" in payload
    assert isinstance(payload["stage_rollout"], dict)
    assert payload["l5_admission"]["shadow_enabled"] is True
    assert payload["l5_admission"]["enforced"] is False
    assert payload["l5_admission"]["metrics"]["l5_total_admitted"] == 4.0


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
    repo_probe_repo = RepoLanguageProbeRepository(db_path)
    probe_repo.upsert_result(
        language="python",
        enabled=True,
        available=True,
        last_probe_at="2026-02-17T00:00:00+00:00",
        last_error_code=None,
        last_error_message=None,
    )
    repo_probe_repo.upsert_state(
        repo_root=str(repo_root.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=2,
        inflight_phase="manual_probe",
        next_retry_at="2026-02-17T00:30:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-02-17T00:00:00+00:00",
        updated_at="2026-02-17T00:00:01+00:00",
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
            "lsp_residual_reap_count": 3,
        },
        repo_hot_checker=lambda repo: repo == str(repo_root.resolve()),
        reconcile_state_provider=lambda: {
            "reconcile_last_run_ts": "2026-02-19T12:00:00+00:00",
            "reconcile_last_result": "ok",
        },
        repo_language_probe_repo=repo_probe_repo,
    )
    payload = tool.call({"repo": str(repo_root.resolve())})

    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    language_support = item["language_support"]
    assert len(language_support["languages"]) >= 1
    assert any(entry["language"] == "python" for entry in language_support["languages"])
    assert item["lsp_metrics"]["lsp_instance_count"] == 4
    assert item["lsp_metrics"]["lsp_orphan_suspect_count"] == 2
    assert item["lsp_metrics"]["lsp_residual_reap_count"] == 3
    assert item["reconcile_state"]["reconcile_last_run_ts"] == "2026-02-19T12:00:00+00:00"
    assert item["reconcile_state"]["reconcile_last_error_code"] is None
    assert item["reconcile_state"]["reconcile_last_error_message"] is None
    assert item["repo_language_probe"]["hot_repo_active"] is True
    assert item["repo_language_probe"]["states"][0]["language"] == "python"
    assert item["repo_language_probe"]["states"][0]["status"] == "BACKPRESSURE_COOLDOWN"
    assert item["repo_language_probe"]["states"][0]["blocked_reason"] == "backpressure"
    assert item["repo_language_probe"]["states"][0]["priority_class"] == "manual_hot"
    assert item["repo_language_probe"]["states"][0]["last_admission_kind"] == "manual_probe"
    assert item["repo_language_probe"]["states"][0]["next_retry_at"] == "2026-02-17T00:30:00+00:00"
    assert "stage_rollout" in item
    assert isinstance(item["stage_rollout"], dict)


def test_mcp_status_uses_validated_repo_root_for_repo_id_inputs(tmp_path: Path) -> None:
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
    RepoRegistryRepository(db_path).upsert(
        RepoIdentityDTO(
            repo_id="rid_repo_a",
            repo_label="repo-a",
            repo_root=str(repo_root.resolve()),
            workspace_root=str(repo_root.resolve()),
            updated_at=now_iso8601_utc(),
        )
    )
    repo_probe_repo = RepoLanguageProbeRepository(db_path)
    repo_probe_repo.upsert_state(
        repo_root=str(repo_root.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-02-17T00:30:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-02-17T00:00:00+00:00",
        updated_at="2026-02-17T00:00:01+00:00",
    )

    seen_repo_roots: list[str] = []
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        repo_language_probe_repo=repo_probe_repo,
        repo_hot_checker=lambda repo: seen_repo_roots.append(repo) or repo == str(repo_root.resolve()),
    )
    payload = tool.call({"repo": "rid_repo_a"})

    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    assert seen_repo_roots == [str(repo_root.resolve())]


def test_mcp_status_priority_class_uses_row_last_trigger(tmp_path: Path) -> None:
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
    repo_probe_repo = RepoLanguageProbeRepository(db_path)
    repo_probe_repo.upsert_state(
        repo_root=str(repo_root.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="background_probe",
        next_retry_at="2026-02-17T00:30:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="background",
        last_seen_at="2026-02-17T00:00:00+00:00",
        updated_at="2026-02-17T00:00:01+00:00",
    )
    repo_probe_repo.upsert_state(
        repo_root=str(repo_root.resolve()),
        language="java",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-02-17T00:31:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="manual_probe",
        last_seen_at="2026-02-17T00:00:00+00:00",
        updated_at="2026-02-17T00:00:02+00:00",
    )
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        repo_language_probe_repo=repo_probe_repo,
        repo_hot_checker=lambda repo: repo == str(repo_root.resolve()),
    )

    payload = tool.call({"repo": str(repo_root.resolve())})

    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    rows = {
        row["language"]: row
        for row in item["repo_language_probe"]["states"]
    }
    assert rows["python"]["priority_class"] == "background"
    assert rows["java"]["priority_class"] == "manual_hot"
    assert item["repo_language_probe"]["hot_repo_active"] is True
    assert rows["python"]["repo_root"] == str(repo_root.resolve())


def test_mcp_status_treats_force_trigger_as_manual_hot(tmp_path: Path) -> None:
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
    repo_probe_repo = RepoLanguageProbeRepository(db_path)
    repo_probe_repo.upsert_state(
        repo_root=str(repo_root.resolve()),
        language="python",
        status="BACKPRESSURE_COOLDOWN",
        fail_count=1,
        inflight_phase="manual_probe",
        next_retry_at="2026-02-17T00:30:00+00:00",
        last_error_code="ERR_LSP_GLOBAL_SOFT_LIMIT",
        last_error_message="soft limit",
        last_trigger="force",
        last_seen_at="2026-02-17T00:00:00+00:00",
        updated_at="2026-02-17T00:00:01+00:00",
    )
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        repo_language_probe_repo=repo_probe_repo,
        repo_hot_checker=lambda repo: repo == str(repo_root.resolve()),
    )

    payload = tool.call({"repo": str(repo_root.resolve())})

    assert payload["isError"] is False
    row = payload["structuredContent"]["items"][0]["repo_language_probe"]["states"][0]
    assert row["priority_class"] == "manual_hot"


def test_mcp_status_aggregates_scope_file_count_and_module_count(tmp_path: Path) -> None:
    """status는 scope_root 기준 file_count/module_repo_count를 집계해야 한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    scope_root = tmp_path / "workspace"
    module_a = scope_root / "mod-a"
    module_b = scope_root / "mod-b"
    module_a.mkdir(parents=True, exist_ok=True)
    module_b.mkdir(parents=True, exist_ok=True)

    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(scope_root.resolve()),
            name="workspace",
            indexed_at=None,
            is_active=True,
        )
    )
    workspace_repo.add(
        WorkspaceDTO(
            path=str(module_a.resolve()),
            name="mod-a",
            indexed_at=None,
            is_active=True,
        )
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, :relative_path, :absolute_path, 'repo',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(module_a.resolve()),
                "scope_repo_root": str(scope_root.resolve()),
                "relative_path": "src/main.py",
                "absolute_path": str((module_a / "src" / "main.py").resolve()),
                "content_hash": "h-a",
            },
        )
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, :relative_path, :absolute_path, 'repo',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(module_b.resolve()),
                "scope_repo_root": str(scope_root.resolve()),
                "relative_path": "src/main.py",
                "absolute_path": str((module_b / "src" / "main.py").resolve()),
                "content_hash": "h-b",
            },
        )
        conn.commit()

    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
    )
    payload = tool.call({"repo": str(scope_root.resolve())})
    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    assert item["scope_repo_root"] == str(scope_root.resolve())
    assert item["file_count"] == 2
    assert item["module_repo_count"] == 2
    assert item["repo_scope_kind"] == "workspace_scope"


def test_mcp_status_falls_back_to_module_repo_count_when_scope_rows_are_missing(tmp_path: Path) -> None:
    """fanout 혼합 shape에서도 module repo 요청 시 file_count가 0으로 왜곡되면 안 된다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    scope_root = tmp_path / "workspace"
    module_a = scope_root / "mod-a"
    module_a.mkdir(parents=True, exist_ok=True)

    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(
        WorkspaceDTO(
            path=str(scope_root.resolve()),
            name="workspace",
            indexed_at=None,
            is_active=True,
        )
    )
    workspace_repo.add(
        WorkspaceDTO(
            path=str(module_a.resolve()),
            name="mod-a",
            indexed_at=None,
            is_active=True,
        )
    )
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, scope_repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, :scope_repo_root, :relative_path, :absolute_path, 'repo',
                1, 10, :content_hash, 0, '2026-02-25T00:00:00+00:00', '2026-02-25T00:00:00+00:00', 'DONE'
            )
            """,
            {
                "repo_root": str(module_a.resolve()),
                "scope_repo_root": str(scope_root.resolve()),
                "relative_path": "src/main.py",
                "absolute_path": str((module_a / "src" / "main.py").resolve()),
                "content_hash": "h-a",
            },
        )
        conn.commit()

    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
    )
    payload = tool.call({"repo": str(module_a.resolve())})
    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    assert item["file_count"] == 1
    assert item["module_repo_count"] == 1
    assert item["repo_scope_kind"] == "module_scope"


def test_mcp_status_exposes_runtime_activity_snapshot(tmp_path: Path) -> None:
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
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        repo_runtime_activity_provider=lambda repo: {
            "repo_root": repo,
            "active_request_count": 2,
            "busy_runtime_count": 1,
            "idle_runtime_count": 3,
        },
    )

    payload = tool.call({"repo": str(repo_root.resolve())})

    item = payload["structuredContent"]["items"][0]
    assert item["runtime_activity"]["active_request_count"] == 2
    assert item["runtime_activity"]["busy_runtime_count"] == 1
    assert item["runtime_activity"]["idle_runtime_count"] == 3


def test_mcp_status_exposes_selective_eviction_metrics(tmp_path: Path) -> None:
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
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        lsp_metrics_provider=lambda: {
            "lsp_selective_eviction_attempt_count": 3,
            "lsp_selective_eviction_success_count": 1,
            "lsp_selective_eviction_skip_hot_repo_count": 1,
            "lsp_selective_eviction_skip_busy_count": 1,
            "lsp_selective_eviction_skip_grace_count": 2,
            "lsp_selective_eviction_skip_post_acquire_idle_count": 1,
        },
    )

    payload = tool.call({"repo": str(repo_root.resolve())})

    item = payload["structuredContent"]["items"][0]
    assert item["lsp_metrics"]["lsp_selective_eviction_attempt_count"] == 3
    assert item["lsp_metrics"]["lsp_selective_eviction_success_count"] == 1
    assert item["lsp_metrics"]["lsp_selective_eviction_skip_hot_repo_count"] == 1
    assert item["lsp_metrics"]["lsp_selective_eviction_skip_busy_count"] == 1
    assert item["lsp_metrics"]["lsp_selective_eviction_skip_grace_count"] == 2
    assert item["lsp_metrics"]["lsp_selective_eviction_skip_post_acquire_idle_count"] == 1
