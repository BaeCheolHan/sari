"""MCP search 도구 오류 승격 정책을 검증한다."""

from __future__ import annotations

from sari.core.models import SearchErrorDTO, SearchItemDTO
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
