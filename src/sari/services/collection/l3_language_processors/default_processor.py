"""Fallback processor for unsupported languages."""

from __future__ import annotations

from ..l3_language_processor import L3LowConfidenceContext, L3LanguageProcessor


class DefaultL3LanguageProcessor(L3LanguageProcessor):
    @property
    def name(self) -> str:
        return "default"

    def supports_path(self, *, relative_path: str) -> bool:
        _ = relative_path
        return True

    def pattern_key(self, *, relative_path: str) -> str | None:
        _ = relative_path
        return None

    def should_route_to_l5(self, *, context: L3LowConfidenceContext) -> bool:
        _ = context
        return True

    def should_replace_symbol_name(
        self,
        *,
        current_name: str,
        candidate_name: str,
        symbol_kind: str,
        symbol_node_type: str,
        name_parent_node_type: str,
        climb_depth: int,
    ) -> bool:
        _ = (current_name, candidate_name, symbol_kind, symbol_node_type, name_parent_node_type, climb_depth)
        return False

    def allows_name_capture_climb(self, *, parent_node_type: str, climb_depth: int) -> bool:
        _ = (parent_node_type, climb_depth)
        return False
