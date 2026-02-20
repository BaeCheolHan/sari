"""HTTP read 엔드포인트 동작을 검증한다."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sari.core.models import WorkspaceDTO
from sari.db.repositories.file_body_repository import FileBodyRepository
from sari.db.repositories.file_collection_repository import FileCollectionRepository
from sari.db.repositories.file_enrich_queue_repository import FileEnrichQueueRepository
from sari.db.repositories.lsp_tool_data_repository import LspToolDataRepository
from sari.db.repositories.knowledge_repository import KnowledgeRepository
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.repositories.tool_readiness_repository import ToolReadinessRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import init_schema
from sari.http.app import (
    HttpContext,
    read_diff_preview_endpoint,
    read_endpoint,
    read_file_endpoint,
)
from sari.search.orchestrator import SearchOrchestrator
from sari.services.admin_service import AdminService
from sari.services.file_collection_service import FileCollectionService
from sari.services.pipeline_quality_service import MirrorGoldenBackend, PipelineQualityService
from sari.services.read_facade_service import ReadFacadeService


class _DummySearchOrchestrator(SearchOrchestrator):  # type: ignore[misc]
    """테스트용 더미 search 오케스트레이터."""

    def __init__(self) -> None:
        """기본 생성 로직을 우회한다."""
        pass

    def search(self, query: str, limit: int, repo_root: str, resolve_symbols: bool = False):  # type: ignore[no-untyped-def]
        """해당 테스트에서는 search를 사용하지 않는다."""
        del query, limit, repo_root, resolve_symbols
        raise AssertionError("search should not be called in read endpoint tests")


class _DummyAdminService(AdminService):  # type: ignore[misc]
    """테스트용 더미 admin 서비스."""

    def __init__(self) -> None:
        """기본 생성 로직을 우회한다."""
        pass

    def doctor(self) -> list[object]:
        """빈 진단 결과를 반환한다."""
        return []


def _build_context(tmp_path: Path) -> tuple[HttpContext, str]:
    """read 엔드포인트 테스트용 HTTP 컨텍스트를 구성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_path = tmp_path / "repo-a"
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    workspace_repo = WorkspaceRepository(db_path)
    workspace_repo.add(WorkspaceDTO(path=str(repo_path), name="repo-a", indexed_at=None, is_active=True))
    runtime_repo = RuntimeRepository(db_path)
    lsp_repo = LspToolDataRepository(db_path)
    file_service = FileCollectionService(
        workspace_repo=workspace_repo,
        file_repo=FileCollectionRepository(db_path),
        enrich_queue_repo=FileEnrichQueueRepository(db_path),
        body_repo=FileBodyRepository(db_path),
        lsp_repo=lsp_repo,
        readiness_repo=ToolReadinessRepository(db_path),
        policy=PipelineQualityService.default_collection_policy(),
        lsp_backend=MirrorGoldenBackend(),
        policy_repo=None,
        event_repo=None,
    )
    file_service.scan_once(repo_root=str(repo_path))
    file_service.process_enrich_jobs(limit=50)
    read_service = ReadFacadeService(
        workspace_repo=workspace_repo,
        file_collection_service=file_service,
        lsp_repo=lsp_repo,
        knowledge_repo=KnowledgeRepository(db_path),
    )
    return (
        HttpContext(
            runtime_repo=runtime_repo,
            workspace_repo=workspace_repo,
            search_orchestrator=_DummySearchOrchestrator(),
            admin_service=_DummyAdminService(),
            file_collection_service=file_service,
            read_facade_service=read_service,
        ),
        str(repo_path),
    )


def test_read_requires_repo(tmp_path: Path) -> None:
    """repo 없이 /read를 호출하면 명시적 오류를 반환해야 한다."""
    context, _ = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"mode": "file", "target": "main.py"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(read_endpoint(request))
    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "ERR_REPO_REQUIRED"


def test_read_file_default_json_success(tmp_path: Path) -> None:
    """기본 JSON 포맷으로 file read 결과를 반환해야 한다."""
    context, repo = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": repo, "mode": "file", "target": "main.py", "limit": "20"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(read_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["relative_path"] == "main.py"


def test_read_file_pack1_format_success(tmp_path: Path) -> None:
    """format=pack1 요청 시 pack1 구조를 반환해야 한다."""
    context, repo = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": repo, "mode": "file", "target": "main.py", "format": "pack1"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(read_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["isError"] is False
    assert "structuredContent" in payload


def test_read_file_split_endpoint(tmp_path: Path) -> None:
    """분리 엔드포인트 /read_file도 동일 동작을 제공해야 한다."""
    context, repo = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": repo, "target": "main.py"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(read_file_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["items"][0]["relative_path"] == "main.py"


def test_read_diff_preview_split_endpoint(tmp_path: Path) -> None:
    """분리 엔드포인트 /read_diff_preview는 POST body를 처리해야 한다."""
    context, repo = _build_context(tmp_path)

    async def _json() -> dict[str, str]:
        return {
            "repo": repo,
            "target": "main.py",
            "content": "def alpha():\n    return 2\n",
        }

    request = SimpleNamespace(
        query_params={},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
        json=_json,
    )
    response = asyncio.run(read_diff_preview_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["path"] == "main.py"


def test_read_prefers_repo_over_repo_id_when_both_present(tmp_path: Path) -> None:
    """repo/repo_id가 동시에 들어오면 repo를 우선 사용해야 한다."""
    context, repo = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": repo, "repo_id": "invalid-repo-id", "mode": "file", "target": "main.py"},
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(read_endpoint(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["relative_path"] == "main.py"
