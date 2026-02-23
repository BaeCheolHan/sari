"""MCP search 도구 오류 승격 정책을 검증한다."""

from __future__ import annotations

from pathlib import Path

from sari.core.models import WorkspaceDTO
from sari.core.models import SearchErrorDTO, SearchItemDTO
from sari.db.repositories.tool_data_layer_repository import ToolDataLayerRepository
from sari.db.repositories.workspace_repository import WorkspaceRepository
from sari.db.schema import connect
from sari.db.schema import init_schema
from sari.mcp.tools.search_tool import SearchTool
from sari.search.orchestrator import SearchMetaDTO, SearchPipelineResult


class _FatalOrchestrator:
    """치명 오류를 반환하는 테스트 오케스트레이터다."""

    def search(self, query: str, limit: int, repo_root: str) -> SearchPipelineResult:
        """치명 오류 메타를 반환한다."""
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
                    ),
                    SearchErrorDTO(
                        code="ERR_LSP_QUERY_FAILED",
                        message="lsp down",
                        severity="FATAL",
                        origin="symbol_resolve",
                    ),
                ],
                fatal_error=True,
                degraded=True,
                error_count=2,
            ),
        )


class _SuccessOrchestrator:
    """정상 결과를 반환하는 테스트 오케스트레이터다."""

    def search(self, query: str, limit: int, repo_root: str) -> SearchPipelineResult:
        """오류 없는 결과를 반환한다."""
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
                resolved_count=1,
                candidate_source="scan",
                errors=[],
                fatal_error=False,
                degraded=False,
                error_count=0,
            ),
        )


class _DegradedOrchestrator:
    """비치명 저하 오류를 반환하는 테스트 오케스트레이터다."""

    def search(self, query: str, limit: int, repo_root: str) -> SearchPipelineResult:
        """비치명 오류 메타를 반환한다."""
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
                resolved_count=1,
                candidate_source="tantivy",
                errors=[
                    SearchErrorDTO(
                        code="ERR_CANDIDATE_BACKEND",
                        message="fallback used",
                        severity="WARN",
                        origin="candidate",
                    )
                ],
                fatal_error=False,
                degraded=True,
                error_count=1,
            ),
        )


class _LockBusyFatalOrchestrator:
    """Tantivy lock busy 치명 오류를 반환하는 테스트 오케스트레이터다."""

    def search(self, query: str, limit: int, repo_root: str) -> SearchPipelineResult:
        """lock busy 치명 오류를 반환한다."""
        del query, limit, repo_root
        return SearchPipelineResult(
            items=[],
            meta=SearchMetaDTO(
                candidate_count=0,
                resolved_count=0,
                candidate_source="backend_error",
                errors=[
                    SearchErrorDTO(
                        code="ERR_TANTIVY_LOCK_BUSY",
                        message="tantivy writer lock busy",
                        severity="FATAL",
                        origin="candidate",
                    )
                ],
                fatal_error=True,
                degraded=True,
                error_count=1,
            ),
        )


class _CaptureArgsOrchestrator:
    """search 인자 전달값을 캡처하는 테스트 오케스트레이터다."""

    def __init__(self) -> None:
        self.last_include_info: bool | None = None
        self.last_symbol_info_budget_sec: float | None = None
        self.last_resolve_symbols: bool | None = None

    def search(
        self,
        query: str,
        limit: int,
        repo_root: str,
        repo_id: str | None = None,
        resolve_symbols: bool = True,
        include_info: bool | None = None,
        symbol_info_budget_sec: float | None = None,
    ) -> SearchPipelineResult:
        del query, limit, repo_root, repo_id
        self.last_resolve_symbols = resolve_symbols
        self.last_include_info = include_info
        self.last_symbol_info_budget_sec = symbol_info_budget_sec
        return SearchPipelineResult(
            items=[],
            meta=SearchMetaDTO(
                candidate_count=0,
                resolved_count=0,
                candidate_source="scan",
                errors=[],
                fatal_error=False,
                degraded=False,
                error_count=0,
            ),
        )


def test_mcp_search_returns_pack1_error_on_fatal() -> None:
    """치명 오류가 있으면 MCP search는 isError=true여야 한다."""
    tool = SearchTool(orchestrator=_FatalOrchestrator())

    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is True
    errors = payload["structuredContent"]["meta"]["errors"]
    assert errors[0]["code"] == "ERR_CANDIDATE_BACKEND"
    assert len(errors) == 2
    assert errors[0]["severity"] == "FATAL"
    assert errors[0]["origin"] == "candidate"
    assert errors[1]["code"] == "ERR_LSP_QUERY_FAILED"
    stabilization = payload["structuredContent"]["meta"]["stabilization"]
    assert "SEARCH_FATAL" in stabilization["reason_codes"]
    assert stabilization["fatal_error"] is True


def test_mcp_search_returns_success_without_fatal() -> None:
    """치명 오류가 없으면 MCP search는 isError=false여야 한다."""
    tool = SearchTool(orchestrator=_SuccessOrchestrator())

    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is False
    assert len(payload["structuredContent"]["items"]) == 1
    meta = payload["structuredContent"]["meta"]
    assert meta["ranking_version"] == "v3-hierarchy"
    assert meta["ranking_components_enabled"]["hierarchy"] is True


def test_mcp_search_sets_stabilization_on_degraded_non_fatal() -> None:
    """비치명 저하 오류는 성공 응답 + stabilization 경고로 전달되어야 한다."""
    tool = SearchTool(orchestrator=_DegradedOrchestrator())

    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is False
    stabilization = payload["structuredContent"]["meta"]["stabilization"]
    assert stabilization["degraded"] is True
    assert stabilization["fatal_error"] is False
    assert "SEARCH_DEGRADED" in stabilization["reason_codes"]
    assert len(stabilization["warnings"]) >= 1


def test_mcp_search_returns_recovery_hint_on_tantivy_lockbusy() -> None:
    """Tantivy lockbusy 치명 오류는 복구 힌트를 포함해야 한다."""
    tool = SearchTool(orchestrator=_LockBusyFatalOrchestrator())
    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is True
    assert payload["structuredContent"]["meta"]["errors"][0]["code"] == "ERR_TANTIVY_LOCK_BUSY"
    assert "proxy" in str(payload["structuredContent"]["error"]["recovery_hint"]).lower()


def test_mcp_search_uses_default_include_info_and_budget_when_not_provided() -> None:
    """인자 미지정 시 SearchTool 생성 기본값을 오케스트레이터로 전달해야 한다."""
    orchestrator = _CaptureArgsOrchestrator()
    tool = SearchTool(
        orchestrator=orchestrator,
        include_info_default=False,
        symbol_info_budget_sec_default=7.5,
    )

    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is False
    assert orchestrator.last_resolve_symbols is False
    assert orchestrator.last_include_info is False
    assert orchestrator.last_symbol_info_budget_sec == 7.5


def test_mcp_search_forwards_include_info_and_budget_arguments() -> None:
    """요청 인자로 전달된 include_info/budget은 우선 적용되어야 한다."""
    orchestrator = _CaptureArgsOrchestrator()
    tool = SearchTool(orchestrator=orchestrator)

    payload = tool.call(
        {
            "repo": "/repo",
            "query": "hello",
            "limit": 5,
            "include_info": True,
            "symbol_info_budget_sec": 1.25,
        }
    )

    assert payload["isError"] is False
    assert orchestrator.last_resolve_symbols is False
    assert orchestrator.last_include_info is True
    assert orchestrator.last_symbol_info_budget_sec == 1.25


def test_mcp_search_forwards_resolve_symbols_when_explicit_true() -> None:
    """resolve_symbols=true를 명시하면 오케스트레이터로 전달되어야 한다."""
    orchestrator = _CaptureArgsOrchestrator()
    tool = SearchTool(orchestrator=orchestrator)

    payload = tool.call(
        {
            "repo": "/repo",
            "query": "hello",
            "limit": 5,
            "resolve_symbols": True,
        }
    )

    assert payload["isError"] is False
    assert orchestrator.last_resolve_symbols is True


def test_mcp_search_uses_runtime_default_resolve_symbols_provider_when_missing() -> None:
    """resolve_symbols 미지정 시 런타임 기본값 provider를 따라야 한다."""
    orchestrator = _CaptureArgsOrchestrator()
    tool = SearchTool(
        orchestrator=orchestrator,
        resolve_symbols_default_provider=lambda: True,
    )

    payload = tool.call({"repo": "/repo", "query": "hello", "limit": 5})

    assert payload["isError"] is False
    assert orchestrator.last_resolve_symbols is True


def test_mcp_search_includes_layer_snapshot_in_item_payload(tmp_path: Path) -> None:
    """tool_data 레이어 스냅샷이 있으면 search item에 병합되어야 한다."""

    class _OneItemOrchestrator:
        def search(
            self,
            query: str,
            limit: int,
            repo_root: str,
            repo_id: str | None = None,
            resolve_symbols: bool = True,
            include_info: bool | None = None,
            symbol_info_budget_sec: float | None = None,
        ) -> SearchPipelineResult:
            del query, limit, repo_id, resolve_symbols, include_info, symbol_info_budget_sec
            return SearchPipelineResult(
                items=[
                    SearchItemDTO(
                        item_type="file",
                        repo=repo_root,
                        relative_path="src/a.py",
                        score=1.0,
                        source="candidate",
                        name=None,
                        kind=None,
                        content_hash="h1",
                    )
                ],
                meta=SearchMetaDTO(
                    candidate_count=1,
                    resolved_count=0,
                    candidate_source="scan",
                    errors=[],
                    fatal_error=False,
                    degraded=False,
                    error_count=0,
                ),
            )

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = str((tmp_path / "repo").resolve())
    Path(repo_root).mkdir(parents=True, exist_ok=True)
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO collected_files_l1(
                repo_id, repo_root, relative_path, absolute_path, repo_label,
                mtime_ns, size_bytes, content_hash, is_deleted, last_seen_at, updated_at, enrich_state
            ) VALUES(
                '', :repo_root, 'src/a.py', :abs_path, 'repo',
                1, 10, 'h1', 0, '2026-02-23T12:00:00Z', '2026-02-23T12:00:00Z', 'READY'
            )
            """,
            {
                "repo_root": repo_root,
                "abs_path": str((Path(repo_root) / "src" / "a.py").resolve()),
            },
        )
        conn.commit()
    layer_repo = ToolDataLayerRepository(db_path)
    layer_repo.upsert_l3_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha"}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at="2026-02-23T12:00:00Z",
    )
    layer_repo.upsert_l4_normalized_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        normalized={"outline": ["Alpha"]},
        confidence=0.95,
        ambiguity=0.1,
        coverage=0.9,
        needs_l5=True,
        updated_at="2026-02-23T12:00:00Z",
    )
    layer_repo.upsert_l5_semantics(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 1},
        updated_at="2026-02-23T12:00:00Z",
    )
    tool = SearchTool(
        orchestrator=_OneItemOrchestrator(),
        workspace_repo=WorkspaceRepository(db_path),
        tool_layer_repo=layer_repo,
    )

    payload = tool.call({"repo": repo_root, "query": "alpha", "limit": 5})

    assert payload["isError"] is False
    items = payload["structuredContent"]["items"]
    assert len(items) == 1
    assert isinstance(items[0]["l4"], dict)
    assert items[0]["l4"]["normalized"]["outline"] == ["Alpha"]
    assert isinstance(items[0]["l5"], list)
    assert items[0]["l5"][0]["reason_code"] == "L5_REASON_UNRESOLVED_SYMBOL"


def test_mcp_search_skips_layer_snapshot_when_active_file_row_missing(tmp_path: Path) -> None:
    """활성 파일 해시를 확인할 수 없으면 L4/L5 병합을 하지 않아야 한다."""

    class _OneItemOrchestrator:
        def search(
            self,
            query: str,
            limit: int,
            repo_root: str,
            repo_id: str | None = None,
            resolve_symbols: bool = True,
            include_info: bool | None = None,
            symbol_info_budget_sec: float | None = None,
        ) -> SearchPipelineResult:
            del query, limit, repo_id, resolve_symbols, include_info, symbol_info_budget_sec
            return SearchPipelineResult(
                items=[
                    SearchItemDTO(
                        item_type="file",
                        repo=repo_root,
                        relative_path="src/a.py",
                        score=1.0,
                        source="candidate",
                        name=None,
                        kind=None,
                        content_hash="h1",
                    )
                ],
                meta=SearchMetaDTO(
                    candidate_count=1,
                    resolved_count=0,
                    candidate_source="scan",
                    errors=[],
                    fatal_error=False,
                    degraded=False,
                    error_count=0,
                ),
            )

    db_path = tmp_path / "state.db"
    init_schema(db_path)
    repo_root = str((tmp_path / "repo").resolve())
    Path(repo_root).mkdir(parents=True, exist_ok=True)
    WorkspaceRepository(db_path).add(WorkspaceDTO(path=repo_root, name="repo", indexed_at=None, is_active=True))
    layer_repo = ToolDataLayerRepository(db_path)
    layer_repo.upsert_l3_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        symbols=[{"name": "Alpha"}],
        degraded=False,
        l3_skipped_large_file=False,
        updated_at="2026-02-23T12:00:00Z",
    )
    layer_repo.upsert_l4_normalized_symbols(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        normalized={"outline": ["Alpha"]},
        confidence=0.95,
        ambiguity=0.1,
        coverage=0.9,
        needs_l5=True,
        updated_at="2026-02-23T12:00:00Z",
    )
    layer_repo.upsert_l5_semantics(
        workspace_id=repo_root,
        repo_root=repo_root,
        relative_path="src/a.py",
        content_hash="h1",
        reason_code="L5_REASON_UNRESOLVED_SYMBOL",
        semantics={"edges": 1},
        updated_at="2026-02-23T12:00:00Z",
    )
    tool = SearchTool(
        orchestrator=_OneItemOrchestrator(),
        workspace_repo=WorkspaceRepository(db_path),
        tool_layer_repo=layer_repo,
    )

    payload = tool.call({"repo": repo_root, "query": "alpha", "limit": 5})

    assert payload["isError"] is False
    items = payload["structuredContent"]["items"]
    assert len(items) == 1
    assert "l4" not in items[0]
    assert "l5" not in items[0]
