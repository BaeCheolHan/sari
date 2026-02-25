"""Kotlin processor."""

from __future__ import annotations

from .base import BaseL3LanguageProcessor, _BaseConfig


class KotlinL3LanguageProcessor(BaseL3LanguageProcessor):
    def __init__(self) -> None:
        super().__init__(
            _BaseConfig(
                name="kotlin",
                extensions=(".kt", ".kts"),
                pattern_key="kotlin",
                min_symbols_for_l3_only=2,
                name_capture_bridge_node_types=(
                    "variable_declaration",
                    "variable_declarator",
                    "class_parameter",
                    "formal_parameter",
                ),
            )
        )
