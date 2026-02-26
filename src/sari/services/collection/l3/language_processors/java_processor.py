"""Java processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class JavaL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="java",
                extensions=(".java",),
                pattern_key="java",
                min_symbols_for_l3_only=2,
            )
        )

