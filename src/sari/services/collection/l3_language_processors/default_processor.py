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

