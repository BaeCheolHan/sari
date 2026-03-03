"""TypeScript processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class TypeScriptL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="typescript",
                extensions=(".ts", ".tsx"),
                pattern_key="ts",
                min_symbols_for_l3_only=2,
            )
        )

