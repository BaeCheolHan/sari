"""Vue processor."""

from __future__ import annotations

from ..l3_language_processor import L3LowConfidenceContext
from .base import BaseL3LanguageProcessor, _BaseConfig


class VueL3LanguageProcessor(BaseL3LanguageProcessor):
    _VUE_MIN_SYMBOLS_FOR_L3_ONLY = 10
    _VUE_IMPORT_HEAVY_MIN_SYMBOLS_FOR_L3_ONLY = 16

    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="vue",
                extensions=(".vue",),
                pattern_key="ts",
                min_symbols_for_l3_only=self._VUE_MIN_SYMBOLS_FOR_L3_ONLY,
            )
        )

    def should_route_to_l5(self, *, context: L3LowConfidenceContext) -> bool:
        threshold = (
            self._VUE_IMPORT_HEAVY_MIN_SYMBOLS_FOR_L3_ONLY
            if context.has_import_like
            else self._VUE_MIN_SYMBOLS_FOR_L3_ONLY
        )
        if context.symbol_count < threshold:
            return True
        return super().should_route_to_l5(context=context)

