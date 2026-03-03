"""search pack1 응답 조립을 담당한다."""

from __future__ import annotations

from sari.mcp.tools.pack1_builder import Pack1EnvelopeBuilder
from sari.mcp.tools.search_item_serializer import SearchItemSerializer


class SearchResponseBuilder:
    """search 결과를 pack1 응답으로 변환한다."""

    def __init__(self, *, envelope_builder: Pack1EnvelopeBuilder, item_serializer: SearchItemSerializer) -> None:
        self._envelope_builder = envelope_builder
        self._item_serializer = item_serializer

    def build_success(
        self,
        *,
        result: object,
        repo_root: str,
        stabilization: dict[str, object] | None,
        progress_meta: dict[str, object] | None,
    ) -> dict[str, object]:
        items = [self._item_serializer.serialize(item=item, repo_root=repo_root) for item in result.items]
        meta_extra: dict[str, object] = {
            "lsp_query_mode": result.meta.lsp_query_mode,
            "lsp_sync_mode": result.meta.lsp_sync_mode,
            "lsp_fallback_used": result.meta.lsp_fallback_used,
            "lsp_fallback_reason": result.meta.lsp_fallback_reason,
            "lsp_include_info_requested": result.meta.include_info_requested,
            "lsp_symbol_info_budget_sec": result.meta.symbol_info_budget_sec,
            "lsp_symbol_info_requested_count": result.meta.symbol_info_requested_count,
            "lsp_symbol_info_budget_exceeded_count": result.meta.symbol_info_budget_exceeded_count,
            "lsp_symbol_info_skipped_count": result.meta.symbol_info_skipped_count,
            "ranking_version": result.meta.ranking_version,
            "ranking_components_enabled": (
                result.meta.ranking_components_enabled if result.meta.ranking_components_enabled is not None else {}
            ),
        }
        if progress_meta is not None:
            meta_extra["index_progress"] = progress_meta
        return self._envelope_builder.build_success(
            items=items,
            candidate_count=result.meta.candidate_count,
            resolved_count=result.meta.resolved_count,
            cache_hit=None,
            errors=[err.to_dict() for err in result.meta.errors],
            stabilization=stabilization,
            meta_extra=meta_extra,
        )

