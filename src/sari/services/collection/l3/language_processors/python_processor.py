"""Python processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class PythonL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="python",
                extensions=(".py",),
                pattern_key="py",
                min_symbols_for_l3_only=2,
            )
        )

