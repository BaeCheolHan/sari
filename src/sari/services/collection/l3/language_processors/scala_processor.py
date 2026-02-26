"""Scala language processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class ScalaL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="scala",
                extensions=(".scala", ".sbt"),
                pattern_key="scala",
                min_symbols_for_l3_only=2,
            )
        )

