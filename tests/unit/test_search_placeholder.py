"""HTTP 검색 입력 검증을 테스트한다."""

import asyncio
import json
from types import SimpleNamespace

from sari.core.config import AppConfig
from sari.core.models import SearchErrorDTO, SearchItemDTO, WorkspaceDTO
from sari.db.repositories.symbol_cache_repository import SymbolCacheRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.http.app import HttpContext, search_endpoint
from sari.search.candidate_search import CandidateSearchService
from sari.search.orchestrator import SearchMetaDTO, SearchOrchestrator, SearchPipelineResult
from sari.search.symbol_resolve import SymbolResolveService
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema
from sari.lsp.hub import LspHub
from sari.services.admin_service import AdminService


def _build_context(tmp_path) -> HttpContext:
    """검색 엔드포인트 테스트 컨텍스트를 구성한다."""
    db_path = tmp_path / "state.db"
    init_schema(db_path)
    workspace_repo = WorkspaceRepository(db_path)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir(parents=True, exist_ok=True)
    workspace_repo.add(WorkspaceDTO(path=str(repo_dir.resolve()), name="repo-a", indexed_at=None, is_active=True))
    runtime_repo = RuntimeRepository(db_path)
    symbol_cache_repo = SymbolCacheRepository(db_path)
    orchestrator = SearchOrchestrator(
        workspace_repo=workspace_repo,
        candidate_service=CandidateSearchService.build_default(
            max_file_size_bytes=512 * 1024,
            index_root=tmp_path / "candidate_index",
            backend_mode="scan",
            enable_scan_fallback=False,
        ),
        symbol_service=SymbolResolveService(hub=LspHub(), cache_repo=symbol_cache_repo),
    )
    admin_service = AdminService(
        config=AppConfig(
            db_path=db_path,
            host="127.0.0.1",
            preferred_port=47777,
            max_port_scan=50,
            stop_grace_sec=10,
        ),
        workspace_repo=workspace_repo,
        runtime_repo=runtime_repo,
        symbol_cache_repo=symbol_cache_repo,
    )
    context = HttpContext(
        runtime_repo=runtime_repo,
        workspace_repo=workspace_repo,
        search_orchestrator=orchestrator,
        admin_service=admin_service,
    )
    context.repo_for_test = str(repo_dir.resolve())  # type: ignore[attr-defined]
    return context


def test_search_invalid_limit_returns_explicit_error(tmp_path) -> None:
    """limit이 정수가 아니면 명시적 오류를 반환하는지 검증한다."""
    context = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": context.repo_for_test, "q": "hello", "limit": "abc"},  # type: ignore[attr-defined]
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(search_endpoint(request))

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "ERR_INVALID_LIMIT"
    assert isinstance(payload["error"]["message"], str)
    assert payload["error"]["message"] != ""


def test_search_empty_query_returns_explicit_error(tmp_path) -> None:
    """q가 비어 있으면 명시적 오류를 반환하는지 검증한다."""
    context = _build_context(tmp_path)
    request = SimpleNamespace(
        query_params={"repo": context.repo_for_test, "q": "", "limit": "5"},  # type: ignore[attr-defined]
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(search_endpoint(request))

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "ERR_QUERY_REQUIRED"
    assert isinstance(payload["error"]["message"], str)
    assert payload["error"]["message"] != ""


class _FatalSearchOrchestrator:
    """치명 검색 오류를 반환하는 테스트 오케스트레이터다."""

    def search(self, query: str, limit: int, repo_root: str) -> SearchPipelineResult:
        """치명 오류 메타와 부분 결과를 반환한다."""
        del query, limit, repo_root
        return SearchPipelineResult(
            items=[
                SearchItemDTO(
                    item_type="file",
                    repo="/repo",
                    relative_path="a.py",
                    score=1.0,
                    source="candidate",
                    name=None,
                    kind=None,
                )
            ],
            meta=SearchMetaDTO(
                candidate_count=1,
                resolved_count=0,
                candidate_source="scan_fallback",
                errors=[
                    SearchErrorDTO(
                        code="ERR_CANDIDATE_BACKEND",
                        message="fallback used: primary failed",
                        severity="FATAL",
                        origin="candidate",
                    )
                ],
                fatal_error=True,
                degraded=True,
                error_count=1,
            ),
        )


def test_search_fatal_error_returns_503(tmp_path) -> None:
    """치명 검색 오류는 503과 명시 오류를 반환해야 한다."""
    context = _build_context(tmp_path)
    context.search_orchestrator = _FatalSearchOrchestrator()  # type: ignore[assignment]
    request = SimpleNamespace(
        query_params={"repo": context.repo_for_test, "q": "hello", "limit": "5"},  # type: ignore[attr-defined]
        app=SimpleNamespace(state=SimpleNamespace(context=context)),
    )
    response = asyncio.run(search_endpoint(request))

    assert response.status_code == 503
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "ERR_CANDIDATE_BACKEND"
    assert payload["meta"]["fatal_error"] is True
    assert payload["meta"]["lsp_query_mode"] == "document_symbol"
    assert payload["meta"]["lsp_sync_mode"] == "did_open_did_change"
    assert payload["meta"]["lsp_fallback_used"] is False
    assert payload["meta"]["lsp_fallback_reason"] is None
    assert "importance_normalize_mode" in payload["meta"]
    assert "importance_max_boost" in payload["meta"]
    assert "vector_applied_count" in payload["meta"]
    assert "vector_skipped_count" in payload["meta"]
    assert "vector_threshold" in payload["meta"]
    assert payload["meta"]["ranking_version"] == "v3-hierarchy"
    assert payload["meta"]["ranking_components_enabled"]["hierarchy"] is True


def test_search_missing_repo_returns_explicit_error(tmp_path) -> None:
    """repo가 비어 있으면 명시적 오류를 반환하는지 검증한다."""
    context = _build_context(tmp_path)
    request = SimpleNamespace(query_params={"q": "hello", "limit": "5"}, app=SimpleNamespace(state=SimpleNamespace(context=context)))
    response = asyncio.run(search_endpoint(request))

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "ERR_REPO_REQUIRED"
