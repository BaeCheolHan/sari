"""JavaScript processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class JavaScriptL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="javascript",
                extensions=(".js", ".jsx", ".mjs", ".cjs"),
                pattern_key="javascript",
                min_symbols_for_l3_only=2,
            )
        )

