"""search 응답 조립기의 노이즈 경로 필터를 검증한다."""

from __future__ import annotations

from types import SimpleNamespace

from sari.mcp.tools.pack1_builder import Pack1EnvelopeBuilder
from sari.mcp.tools.search_item_serializer import SearchItemSerializer
from sari.mcp.tools.search_response_builder import SearchResponseBuilder


def _meta_stub() -> object:
    return SimpleNamespace(
        lsp_query_mode="document_symbol",
        lsp_sync_mode="did_open_did_change",
        lsp_fallback_used=False,
        lsp_fallback_reason=None,
        include_info_requested=False,
        symbol_info_budget_sec=0.0,
        symbol_info_requested_count=0,
        symbol_info_budget_exceeded_count=0,
        symbol_info_skipped_count=0,
        ranking_version="v3-hierarchy",
        ranking_components_enabled={"rrf": True},
        candidate_count=2,
        resolved_count=2,
        errors=[],
    )


def test_build_success_filters_build_lib_noise_items() -> None:
    builder = SearchResponseBuilder(
        envelope_builder=Pack1EnvelopeBuilder(),
        item_serializer=SearchItemSerializer(workspace_repo=None, tool_layer_repo=None),
    )
    result = SimpleNamespace(
        items=[
            SimpleNamespace(
                item_type="symbol",
                repo="/repo-a",
                relative_path="build/lib/sari/http/routes/main.py",
                score=1.0,
                source="candidate",
                name="status_endpoint",
                kind="function",
                symbol_info=None,
                content_hash="h-build",
            ),
            SimpleNamespace(
                item_type="symbol",
                repo="/repo-a",
                relative_path="src/sari/http/meta_endpoints.py",
                score=2.0,
                source="candidate",
                name="status_endpoint",
                kind="function",
                symbol_info=None,
                content_hash="h-src",
            ),
        ],
        meta=_meta_stub(),
    )

    payload = builder.build_success(
        result=result,
        repo_root="/repo-a",
        stabilization=None,
        progress_meta=None,
    )

    assert payload["isError"] is False
    items = payload["structuredContent"]["items"]
    assert len(items) == 1
    assert items[0]["relative_path"] == "src/sari/http/meta_endpoints.py"
