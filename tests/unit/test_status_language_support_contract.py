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
from sari.mcp.tools.legacy_tools import StatusTool


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
        admin_service=SimpleNamespace(),
        language_probe_repo=probe_repo,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(context=context)))
    response = asyncio.run(status_endpoint(request))

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
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
    tool = StatusTool(
        workspace_repo=workspace_repo,
        runtime_repo=RuntimeRepository(db_path),
        file_repo=FileCollectionRepository(db_path),
        lsp_repo=LspToolDataRepository(db_path),
        language_probe_repo=probe_repo,
    )
    payload = tool.call({"repo": str(repo_root.resolve())})

    assert payload["isError"] is False
    item = payload["structuredContent"]["items"][0]
    language_support = item["language_support"]
    assert len(language_support["languages"]) >= 1
    assert any(entry["language"] == "python" for entry in language_support["languages"])
